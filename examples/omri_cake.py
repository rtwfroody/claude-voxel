"""A three-tier birthday cake for Omri.

    python3 examples/omri_cake.py            # write omri_cake.vox
    python3 examples/omri_cake.py --preview  # ...and show it in the terminal

Everything is deterministic: the "random" drips and sprinkles come from a
seeded RNG, so rebuilding gives byte-identical output.
"""

import math
import os
import random
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from voxel import Model, shapes  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "omri_cake.vox")
SEED = 20260725

# Candles in the ring on top -- set this to Omri's age.
CANDLE_COUNT = 6

# -- palette ---------------------------------------------------------------
PLATE      = "#cfd6dc"
PLATE_RIM  = "#aab3bb"
FROST_A    = "#fdf0e0"   # cream
FROST_B    = "#f6b6c8"   # pink
GANACHE    = "#5c3520"
SPONGE     = "#e9c37f"
JAM        = "#c8385a"
PEARL      = "#fff6ea"
TEXT       = "#e0457b"
CANDLE_A   = "#fbfbfb"
CANDLE_B   = "#e33b3b"
WICK       = "#2a2118"
FLAME_OUT  = "#f5851f"
FLAME_IN   = "#ffe45c"
SPRINKLES  = ["#3fb0e8", "#ffd83d", "#7ad14f", "#ff6fae", "#a86fe8", "#ff8a3d"]

# -- tiers, bottom to top --------------------------------------------------
class Tier(NamedTuple):
    r: int              # radius
    h: int              # height
    z: int              # base z
    frost: str          # shell color
    drip_base: float    # shortest ganache drip
    drip_amp: float     # extra length at the longest drip


TIERS = [
    Tier(36, 18, 0,  FROST_A, 2, 5),
    Tier(26, 15, 18, FROST_B, 2, 5),
    Tier(17, 13, 33, FROST_A, 2, 4),
]

# 5x7 font, just the four letters we need.
FONT = {
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
}


def disc_cells(r_outer, r_inner=-1.0):
    """(x, y) cells with r_inner < distance-from-axis <= r_outer + 0.5."""
    lim = int(r_outer) + 1
    return [(x, y) for x in range(-lim, lim + 1) for y in range(-lim, lim + 1)
            if r_inner < math.hypot(x, y) <= r_outer + 0.5]


def wobble(theta, phases):
    """Smooth 0..1 noise around a circle, for organic drip lengths."""
    v = sum(math.sin(k * theta + p) for k, p in phases) / len(phases)
    return (v + 1.0) / 2.0


def build():
    rng = random.Random(SEED)
    m = Model()

    # -- plate ------------------------------------------------------------
    m.cylinder((0, 0, -3), 46, 3, PLATE)
    m.add(shapes.cylinder((0, 0, -1), 46, 1) - shapes.cylinder((0, 0, -1), 43, 1),
          PLATE_RIM)

    # -- tiers ------------------------------------------------------------
    for i, t in enumerate(TIERS):
        r, h, z0 = t.r, t.h, t.z
        z_top = z0 + h - 1
        outer = shapes.cylinder((0, 0, z0), r, h)
        inner = shapes.cylinder((0, 0, z0 + 2), r - 2, h - 4)

        # Sponge interior with jam layers, so a cut-away reads correctly.
        m.add(inner, SPONGE)
        for zz in range(z0 + 2, z0 + h - 2):
            if (zz - z0) % 5 == 0:
                m.add({c for c in inner if c[2] == zz}, JAM)

        # Frosted shell, 2 voxels thick, caps included.
        m.add(outer - inner, t.frost)

        # Ganache pooled on the exposed top: the full disc on the top tier,
        # an annulus on the ones with a tier sitting on them.
        next_r = TIERS[i + 1].r if i + 1 < len(TIERS) else -1.0
        pool = disc_cells(r, next_r + 1 if next_r > 0 else -1.0)
        for x, y in pool:
            m.voxel((x, y, z_top), GANACHE)

        # Drips running down the side from the top rim.
        phases = [(k, rng.uniform(0, 2 * math.pi)) for k in (5, 9, 14)]
        for x, y in disc_cells(r, r - 2.0):
            length = t.drip_base + t.drip_amp * wobble(
                math.atan2(y, x), phases)
            for zz in range(z_top - int(round(length)), z_top + 1):
                m.voxel((x, y, zz), GANACHE)

        # Piped pearls around the base of the tier.
        circumference = 2 * math.pi * r
        for j in range(int(circumference / 5)):
            a = 2 * math.pi * j / int(circumference / 5)
            px, py = round((r + 1) * math.cos(a)), round((r + 1) * math.sin(a))
            m.add(shapes.sphere((px, py, z0 + 1), 1.6), PEARL)

    top = TIERS[-1]
    top_z = top.z + top.h - 1

    # -- sprinkles on the ganache top -------------------------------------
    for _ in range(90):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(0, top.r - 2)
        x, y = round(d * math.cos(a)), round(d * math.sin(a))
        colour = rng.choice(SPRINKLES)
        m.voxel((x, y, top_z + 1), colour)
        if rng.random() < 0.5:                      # half are two-voxel dashes
            m.voxel((x + rng.choice([-1, 1]), y, top_z + 1), colour)

    # -- candles ----------------------------------------------------------
    for j in range(CANDLE_COUNT):
        a = 2 * math.pi * j / CANDLE_COUNT + 0.3
        cx, cy = round(10 * math.cos(a)), round(10 * math.sin(a))
        base = top_z + 1          # directly on the ganache; +2 leaves a gap
        height = 12
        for zz in range(base, base + height):       # barber-pole stripes
            band = CANDLE_A if ((zz - base) // 2) % 2 == 0 else CANDLE_B
            m.add({c for c in shapes.cylinder((cx, cy, zz), 1, 1)}, band)
        wick_z = base + height
        m.voxel((cx, cy, wick_z), WICK)
        # Centre the flame 5 up: at +3 the ellipsoid's z-radius swallows the
        # wick voxel entirely, leaving it invisible.
        m.add(shapes.ellipsoid((cx, cy, wick_z + 5), (2, 2, 3.5)), FLAME_OUT)
        m.add(shapes.ellipsoid((cx, cy, wick_z + 5), (1, 1, 2)), FLAME_IN)

    # -- "OMRI" piped onto the front of the base tier ---------------------
    base_r = TIERS[0].r
    word = "OMRI"
    width = len(word) * 5 + (len(word) - 1)         # 5 wide, 1 space between
    x0 = -(width // 2)
    text_z = 3                                      # below the drip zone
    for li, ch in enumerate(word):
        for row, line in enumerate(FONT[ch]):
            for col, pix in enumerate(line):
                if pix != "#":
                    continue
                x = x0 + li * 6 + col
                z = text_z + (6 - row)              # row 0 is the top
                # Sit the pixel on the curved front face, one voxel proud.
                y = -int(math.sqrt((base_r + 0.5) ** 2 - x ** 2))
                m.voxel((x, y, z), TEXT)
                m.voxel((x, y - 1, z), TEXT)

    return m


if __name__ == "__main__":
    cake = build()
    print(cake.save(OUT))
    if "--preview" in sys.argv:
        print(cake.preview(max_dim=64, ansi="--ansi" in sys.argv))
