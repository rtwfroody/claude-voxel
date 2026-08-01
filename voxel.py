"""voxel.py -- author MagicaVoxel .vox files from Python. No dependencies.

Coordinate system matches MagicaVoxel: right-handed, Z up.
    +X right, +Y away from viewer (depth), +Z up.

Models are sparse and accept negative coordinates while you build; save()
normalizes the min corner to the origin and enforces the 256^3 limit.

    from voxel import Model
    m = Model()
    m.sphere((0, 0, 0), 8, "red")
    m.box((-10, -10, -12), (10, 10, -10), "stone")
    print(m.preview())
    m.save("ball.vox")

Shapes are plain sets of (x, y, z) tuples, so set algebra composes them:

    from voxel import shapes
    shell = shapes.sphere((0, 0, 0), 8) - shapes.sphere((0, 0, 0), 6)
    m.add(shell, "glass")

A single model is capped at 256 per axis by the format. Scene bins voxels
into 256^3 chunks and writes them as a multi-model file, so a world can be
any size:

    from voxel import Scene
    s = Scene()
    s.place(build_tower(), offset=(700, 300, 0))
    s.save("world.vox")
"""

from __future__ import annotations

import bisect
import math
import os
import struct
import sys
import zlib
from collections import Counter

MAX_DIM = 256  # per-axis limit imposed by the .vox format (coords are uint8)


# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

#: Named colors, chosen to cover the usual "build a recognizable object" needs.
NAMED_COLORS = {
    "black": "#1a1a1a",      "white": "#f2f2f2",      "grey": "#808080",
    "gray": "#808080",       "light_grey": "#c0c0c0", "dark_grey": "#404040",
    "light_gray": "#c0c0c0", "dark_gray": "#404040",
    "red": "#d13b3b",        "dark_red": "#8c1f1f",   "pink": "#f0a0b8",
    "orange": "#e8892b",     "brown": "#7a5230",      "dark_brown": "#4a3020",
    "tan": "#c9a06a",        "sand": "#e0cf9a",       "yellow": "#f0d040",
    "gold": "#d4a017",       "lime": "#8fd44a",       "green": "#3f9e46",
    "dark_green": "#22602c", "leaf": "#4f9c3a",       "teal": "#2f8f8f",
    "cyan": "#4fd0d0",       "blue": "#3a6fd8",       "dark_blue": "#1f3f8c",
    "navy": "#16264f",       "sky": "#8ec6f0",        "purple": "#8a4fc0",
    "magenta": "#c94fb0",    "skin": "#e8b48c",       "skin_dark": "#a9714a",
    "stone": "#8a8a8a",      "dark_stone": "#5a5a5a", "wood": "#96682e",
    "metal": "#9aa4ad",      "steel": "#6d7880",      "copper": "#b5713a",
    "silver": "#c8ccd0",     "water": "#3f7fd0",      "lava": "#e0561f",
    "glass": "#a8d8e8",      "bone": "#e6dfc8",
}


def parse_color(c):
    """Coerce a color spec to an (r, g, b, a) tuple of ints 0-255.

    Accepts a name from NAMED_COLORS, "#rgb"/"#rrggbb"/"#rrggbbaa", or an
    (r, g, b) / (r, g, b, a) sequence, whose members may be floats.

    This is the one place a color becomes integral, and it is deliberately the
    *last* place: the color operations below return floats so that a derived
    color keeps full precision until it is painted or printed. See the note
    above `scale_color`.
    """
    if isinstance(c, str):
        s = NAMED_COLORS.get(c.lower().replace(" ", "_"), c).lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) == 6:
            s += "ff"
        if len(s) != 8:
            raise ValueError(f"unrecognized color {c!r}")
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))
        except ValueError:
            raise ValueError(f"unrecognized color {c!r}") from None
    vals = tuple(int(round(v)) for v in c)
    if len(vals) == 3:
        vals += (255,)
    if len(vals) != 4 or not all(0 <= v <= 255 for v in vals):
        raise ValueError(f"unrecognized color {c!r}")
    return vals


def _channels(color):
    """A color spec as four float channels, keeping a numeric input's precision.

    The read side of the precision contract: `parse_color` would round a
    computed color on the way *in*, which loses just as much as rounding on
    the way out. Names and hex strings are integral anyway.
    """
    if isinstance(color, str):
        return tuple(float(v) for v in parse_color(color))
    vals = tuple(float(v) for v in color)
    if len(vals) == 3:
        vals += (255.0,)
    if len(vals) != 4 or not all(0.0 <= v <= 255.0 for v in vals):
        raise ValueError(f"unrecognized color {color!r}")
    return vals


def _clamp_rgba(vals):
    """Clamp four channel values into range, *without* rounding them.

    Rounding here is what a color operation must not do -- see the note above
    `scale_color`.
    """
    return tuple(max(0.0, min(255.0, float(v))) for v in vals)


def to_hex(color):
    """Any color spec as "#rrggbb", or "#rrggbbaa" when it is translucent.

    The printable form, and one of the two places a color rounds. Use it for
    logs, dict keys and eyeballing a computed color against a measured one.
    """
    rgba = parse_color(color)
    if rgba[3] == 255:
        return "#%02x%02x%02x" % rgba[:3]
    return "#%02x%02x%02x%02x" % rgba


def luma(color):
    """Rec.601 perceived brightness, 0-255.

    The one number to compare two colors by when you care how light they look
    rather than what hue they are.
    """
    r, g, b, _ = _channels(color)
    return 0.299 * r + 0.587 * g + 0.114 * b


def chroma(color):
    """Colorfulness as max channel minus min, 0-255; 0 is a pure grey.

    Mostly useful as a *check*: it is the quantity `relight` holds fixed and
    `scale_color` moves, so measuring it before and after tells you which of
    the two you actually applied.
    """
    r, g, b, _ = _channels(color)
    return float(max(r, g, b) - min(r, g, b))


# Every operation from here down returns **floats**, clamped to 0-255 but not
# rounded, and composes with the others without losing precision. A color
# rounds in exactly two places: `parse_color`, when it is painted, and
# `to_hex`, when it is printed.
#
# That is not fussiness. These functions are usually the *first step of a
# chain* -- integrate a measured ramp, then tint the result into a family of
# ten colors -- and rounding each step propagates. A mean channel that lands
# on 182.5 becomes 182 before anything downstream sees it, and every tint
# derived from it is then a unit low. The error is a single count in one
# channel, far too small to see and far too small for any check to flag, but
# it moves every color in the family and it is not what the measurement said.
#
# The corollary is about testing, and it cost a wrong "exact" claim here: a
# helper at the head of a chain cannot be validated by comparing its own
# return value. Compare the end of the chain.

# The two brightness operations below both multiply `luma` by `k`, and differ
# in everything else. Picking the wrong one is silent -- the result is a
# plausible color of the right brightness -- so choose by what you are
# modelling, not by which reads better:
#
#   scale_color   multiplies every channel.  Chroma scales with brightness.
#                 This is what light physically does: a darker surface of the
#                 same material reflects less of every wavelength. Use it for
#                 albedo, shading, tinting a base color into a family.
#
#   relight       adds one offset to every channel.  Chroma is unchanged.
#                 This is an exposure correction: it says "the reference photo
#                 was shot too dark, but I believe its colorfulness". Use it
#                 when the brightness is known wrong and the color is not.

def scale_color(color, k):
    """Multiply every channel by `k`. Luma scales by `k`; so does chroma.

    The physical operation -- halving a surface's albedo halves what it
    reflects at every wavelength -- so this is the honest way to darken or
    lighten a *material*. Hue is preserved exactly, and so is every ratio
    inside the color, which is why a family of tints of one base still reads
    as one substance.

    See `relight` for the version that leaves chroma alone, and note that
    channels clamp at 255: scaling an already-bright color up will desaturate
    it as channels pile into the ceiling.
    """
    r, g, b, a = _channels(color)
    return _clamp_rgba((r * k, g * k, b * k, a))


def relight(color, k):
    """Shift every channel so luma scales by `k`. Chroma is unchanged.

    Adding one offset to all three channels moves luma by that offset and
    leaves every channel *difference* -- which is what chroma is -- untouched.
    That is the operation wanted when a reference's brightness is known to be
    wrong (it was shot underexposed) but its colorfulness is believed.

    `scale_color` would drag chroma along with luma, so a 1.6x brightness fix
    would silently become a 1.6x saturation increase. That mistake is the
    reason these are two functions instead of one.
    """
    r, g, b, a = _channels(color)
    d = luma(color) * (k - 1.0)
    return _clamp_rgba((r + d, g + d, b + d, a))


# -- color ramps ------------------------------------------------------------
# A ramp is [(position, color), ...] with strictly ascending positions: a
# measured gradient, sampled wherever you could read a value off the
# reference. What you do with one is read a color out of it (`ramp_at`) or
# reduce it to a single color (`disc_average`).

def ramp_at(ramp, t):
    """Color at position `t` along a ramp, interpolated and clamped at the ends."""
    if not ramp:
        raise ValueError("ramp is empty")
    ts = [k[0] for k in ramp]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        raise ValueError("ramp positions must be strictly ascending")
    if t <= ts[0]:
        return _channels(ramp[0][1])
    for (t0, c0), (t1, c1) in zip(ramp, ramp[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            a, b = _channels(c0), _channels(c1)
            return _clamp_rgba(tuple(a[i] + (b[i] - a[i]) * f for i in range(4)))
    return _channels(ramp[-1][1])


def disc_average(ramp, steps=2000):
    """The one color a flat disc should be, given its center-to-rim ramp.

        integral(0..1) c(u) * 2u du  /  integral(0..1) 2u du

    `ramp` is positioned in *radius fraction*, 0 at the center and 1 at the
    rim. The area of the annulus at `u` goes as `u`, so the outside of a disc
    carries far more of it than the middle does -- 75% of a disc's area lies
    outside u = 0.5. Averaging the ramp's entries, or reading the middle of
    the table, therefore lands much too close to the center color.

    Photographs of round things are the usual source of a ramp like this, and
    the usual mistake is to paint the object the color the middle of the photo
    shows. Integrate instead.
    """
    num = [0.0, 0.0, 0.0, 0.0]
    den = 0.0
    for i in range(steps):
        u = (i + 0.5) / steps
        w = 2.0 * u
        c = ramp_at(ramp, u)
        for k in range(4):
            num[k] += w * c[k]
        den += w
    return _clamp_rgba(tuple(n / den for n in num))


# -- curves: the two shapes a hand-measured profile comes in -----------------

def interp(knots, x):
    """Linear interpolation over `[(x, y), ...]` ascending, clamped at both ends.

    The way to carry a *measured* profile into a build: a shoreline read off a
    photograph as a handful of (depth, width) pairs, a bell's radius at eight
    heights, a wing chord at five span fractions. Knots outside the table
    return its end values rather than extrapolating, which is what you want
    for a profile -- a table that stops is a shape that stops, not a shape
    that keeps going.

        SHORE = ((-75, -100), (-45, -95), (-25, -86), (0, -62))
        left_x = int(round(interp(SHORE, y)))

    Returns whatever the arithmetic gives and never rounds; round at the point
    you paint, the same rule the color operations follow. This lived here
    privately for a while and a build wrote its own copy anyway, purely
    because it was not exported.
    """
    if x <= knots[0][0]:
        return knots[0][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return knots[-1][1]


def smoothstep(t):
    """Clamped cubic ease: 0 below 0, 1 above 1, `t*t*(3-2t)` between.

    The falloff to reach for whenever one region has to blend into another --
    a taper out from under a bridge, a bank sloping into water, the run-in of
    a willow's shadow across a pond. A linear ramp leaves a visible crease at
    both ends of the blend because its slope jumps; this one arrives flat.

        weight = smoothstep(distance / TAPER)      # 1 at the feature, 0 past it

    The clamp is the whole point of naming it: the argument is nearly always a
    distance divided by a run length, which goes negative on one side and past
    1 on the other, and an unclamped cubic turns back around outside [0, 1].
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


# -- matching a source's values onto measured colors -------------------------
# Reusing someone else's map, image or heightfield for its *structure* while
# taking every color from your own measurements. Both functions take
# `weights`: {source value: how much of the picture it covers}. What "covers"
# means is yours to decide -- a plain cell count, or an area weight when the
# cells are not equal-area (see `equirect_shares` in the solar-system build
# for the spherical case, where raw counts give the poles five times their
# share).

def weighted_quantiles(weights, fractions):
    """Cut values splitting a weighted distribution at cumulative `fractions`.

    Returns one value per fraction; bucket `k` is everything with
    `cuts[k-1] < v <= cuts[k]`, so the cuts are inclusive upper bounds and
    there is one more bucket than there are cuts.

    Every cut lands on a **whole value boundary**, which is the point of this
    over a plain interpolated quantile. A quantised source holds only a
    handful of distinct values, so a cut placed at the exact requested
    fraction almost always falls *inside* a tied group of them. Splitting
    there either dithers identical cells between two buckets or, if the
    comparison is inclusive, empties the bucket above it entirely and loses a
    color with no other symptom. Each cut is instead the value boundary
    nearest its requested fraction, and the cuts are forced strictly
    increasing so every bucket keeps at least one value.

    Achieved coverage therefore differs from what was asked for, by however
    much the snapping moved each cut. If something downstream depends on the
    split -- a mean brightness, say -- measure it from the result rather than
    computing it from `fractions`.
    """
    vals = sorted(weights)
    total = float(sum(weights.values()))
    if not vals or total <= 0:
        raise ValueError("weights are empty or sum to zero")
    cum, acc = [], 0.0
    for v in vals:
        acc += weights[v]
        cum.append(acc / total)

    cuts, lo = [], 0
    for k, f in enumerate(fractions):
        hi = len(vals) - 1 - (len(fractions) - k)      # leave room above
        if lo > hi:
            raise ValueError(
                f"{len(vals)} distinct values cannot be cut into "
                f"{len(fractions) + 1} non-empty buckets"
            )
        j = min(range(lo, hi + 1), key=lambda t: abs(cum[t] - f))
        cuts.append(vals[j])
        lo = j + 1
    return cuts


def share_fractions(shares):
    """The cumulative `fractions` that give each bucket its share.

    `weighted_quantiles` takes **cumulative boundaries** -- one fewer than the
    number of buckets -- and a list of per-bucket shares looks exactly like
    them at the call site. A 20/60/20 three-tone split is `[0.20, 0.80]`, not
    `[0.20, 0.60, 0.20]`; the latter asks for *four* buckets cut at
    p20/p60/p80 and is wrong in a way nothing downstream complains about. A
    curtain measured at 34% dark / 58% mid / 8% glint was cut at p34/p58 and
    painted 34/24/42 instead: three rounds of blind identification called the
    result stone, because 42% of it had gone to the pale glint tone. Convert
    the measured shares and hand the result on:

        cuts = weighted_quantiles(field, share_fractions([0.20, 0.60, 0.20]))

    `shares` need not sum to 1 -- they are normalized, so raw area
    percentages, pixel counts or relative weights all work.
    """
    shares = list(shares)
    total = float(sum(shares))
    if not shares or total <= 0:
        raise ValueError("shares are empty or sum to zero")
    out, acc = [], 0.0
    for w in shares[:-1]:
        acc += w / total
        out.append(acc)
    return out


def bucket_by_cuts(items, values, cuts):
    """Group `items` into `len(cuts) + 1` sets by where each value falls.

    `values` is one scalar per item, in the same order. The cuts are inclusive
    upper bounds and must ascend -- bucket `k` holds `cuts[k-1] < v <= cuts[k]`
    -- which is `weighted_quantiles`' convention, so its output drops straight
    in here. Returns a list of sets, darkest bucket first if the values are
    ranked dark to light.

    Use this directly when the thresholds are themselves measured (a luma
    boundary read off a reference); use `bucket_by_shares` when what you
    measured is how much *area* each bucket covers.
    """
    groups = [set() for _ in range(len(cuts) + 1)]
    for item, v in zip(items, values):
        groups[bisect.bisect_left(cuts, v)].add(item)
    return groups


def bucket_by_shares(items, values, shares):
    """Group `items` so each bucket gets its measured share of them.

    The one call that turns "this surface is 20% shadow, 60% mid, 20% lit" and
    a noise field into paintable sets:

        vals = [fbm3(x * S, y * S, z * S, SEED) for x, y, z in cells]
        for color, group in zip(RAMP, bucket_by_shares(cells, vals, SHARES)):
            m.add(group, color)

    Doing it by hand is three steps and every one of them has bitten a build in
    this repo. `fbm3` is nowhere near uniform, so thresholds that look like the
    shares select nothing like them; `weighted_quantiles` fixes that but wants
    *cumulative* boundaries, so passing the shares straight in silently asks
    for one bucket too many; and the quantiles have to come from the values
    over the coordinates actually being painted, not from a larger region.
    Here `shares` means shares, `len(shares)` buckets come back, and the cuts
    are taken over `values` by construction.

    Values are weighted by how many items carry them, so a quantised field
    (an imported map with eight levels) splits by area rather than by how many
    distinct levels sit below the cut. `shares` need not sum to 1.

    Achieved shares differ a little from the ones asked for, because
    `weighted_quantiles` snaps each cut to a whole value boundary. Measure the
    result if it matters -- `color_histogram()` against the intended shares is
    the check, and it has caught a curtain painted 34/24/42 against an
    intended 34/58/8.
    """
    cuts = weighted_quantiles(Counter(values), share_fractions(shares))
    return bucket_by_cuts(items, values, cuts)


def rank_normalize(values):
    """Replace every value by its rank in the sample, spread over [0, 1].

    Takes `{key: value}` and returns `{key: rank}`; any other iterable returns
    a list of ranks in the same order. The smallest value maps to 0.0 and the
    largest to 1.0, so `lo + (hi - lo) * rank` really does span `lo..hi`.

    This is what to reach for when a **continuous** quantity is driven by
    `fbm3` -- a heightfield, a depth, a tint strength. fbm3 piles up in the
    middle (p20 0.40, p50 0.52, p80 0.62), so `base + amp * fbm3(...)` only
    ever spends the middle fifth of `base..base + amp`: a bank meant to clump
    and dip came out a ridge of near-constant height, and three separate
    viewers called it a wall. Ranking the field first spends all of the range.
    Use `weighted_quantiles` instead when the output is a handful of buckets
    rather than a continuum -- the two answer different questions.

    Sample the field over the coordinates you are actually going to paint, not
    over some larger region, or the ranks describe a distribution you never
    show:

        field = {p: fbm3(p[0] * s, p[1] * s, p[2] * s, seed) for p in coords}
        for p, r in rank_normalize(field).items():
            m.voxel(p, ramp_at(RAMP, r))

    Ties all take the rank of the bottom of their group, so a constant field
    comes back all zeros rather than all 0.5.
    """
    items = list(values.items()) if hasattr(values, "items") else None
    seq = [v for _, v in items] if items is not None else list(values)
    order = sorted(seq)
    n = max(1, len(order) - 1)
    ranks = [bisect.bisect_left(order, v) / n for v in seq]
    if items is None:
        return ranks
    return {k: r for (k, _), r in zip(items, ranks)}


def match_histogram(weights, ramp, distribution=None, key=None):
    """Give every source value the ramp color at its own cumulative rank.

    Returns {source value: color}. Values are ranked in sorted order (pass
    `key` if the natural order is not dark-to-light), and each one is placed
    at the midpoint of the span it covers -- so a value covering half the
    picture lands at rank 0.25 if it is the darkest, not at 0.5.

    This is a **histogram match**, and the alternative that looks equivalent
    is not. Rescaling the source's range linearly onto the measured range
    assumes the two distributions have the same *shape*; when they do not, the
    result is wrong in a way that passes every check, because it has the right
    extremes, the right structure and a plausible spread. Matching by rank
    fixes it by construction: every source value is placed at the measured
    brightness that has the same fraction of the picture below it.

    `ramp` is the measured colors at their own measured positions.
    `distribution` is the measured spread as [(cumulative fraction, ramp
    position), ...] -- how the target's own brightness is distributed -- and
    defaults to the identity, i.e. rank maps straight onto ramp position.
    """
    order = sorted(weights, key=key) if key is not None else sorted(weights)
    total = float(sum(weights.values()))
    if not order or total <= 0:
        raise ValueError("weights are empty or sum to zero")
    out, cum = {}, 0.0
    for v in order:
        share = weights[v] / total
        t = cum + share / 2.0
        out[v] = ramp_at(ramp, interp(distribution, t)
                         if distribution is not None else t)
        cum += share
    return out


class Palette:
    """Interns colors into .vox palette indices 1-255 (0 means empty)."""

    def __init__(self):
        self._colors = []          # index i holds the color for palette index i+1
        self._lookup = {}

    def index(self, color):
        """Return the palette index for `color`, adding it if it is new.

        An int in 1..255 passes through untouched, for files where you want to
        target specific palette slots.
        """
        if isinstance(color, int) and not isinstance(color, bool):
            if not 1 <= color <= 255:
                raise ValueError(f"palette index {color} outside 1..255")
            while len(self._colors) < color:
                self._colors.append((0, 0, 0, 255))
            return color
        rgba = parse_color(color)
        if rgba in self._lookup:
            return self._lookup[rgba]
        if len(self._colors) >= 255:
            raise ValueError(
                "palette full: .vox supports 255 colors, and this model "
                f"already uses {len(self._colors)}"
            )
        self._colors.append(rgba)
        idx = len(self._colors)
        self._lookup[rgba] = idx
        return idx

    def rgba(self, index):
        """Color at a palette index, or opaque black if never assigned."""
        if 1 <= index <= len(self._colors):
            return self._colors[index - 1]
        return (0, 0, 0, 255)

    def name(self, index):
        """Best-effort human label for a palette index, for legends."""
        rgba = self.rgba(index)
        for label, spec in NAMED_COLORS.items():
            if parse_color(spec) == rgba:
                return label
        return "#%02x%02x%02x" % rgba[:3]

    def chunk_bytes(self):
        """The 1024-byte RGBA chunk payload.

        The format's notorious off-by-one lives here: entry i of this table is
        palette index i+1, so index 255 is unreachable and the last slot is
        padding.
        """
        table = list(self._colors) + [(0, 0, 0, 0)] * (256 - len(self._colors))
        return b"".join(bytes(c) for c in table[:256])

    def __len__(self):
        return len(self._colors)


# --------------------------------------------------------------------------
# value noise -- seeded, reproducible, and the same on every machine
# --------------------------------------------------------------------------

def _hash01(ix, iy, iz, seed):
    h = (ix * 374761393 + iy * 668265263 + iz * 2147483647 + seed * 1274126177)
    h &= 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177
    h &= 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFF) / float(0xFFFFFF)


def _smooth(t):
    return t * t * (3.0 - 2.0 * t)


def noise3(x, y, z, seed=0):
    """Seeded value noise in [0, 1], trilinear with a smoothstep fade.

    A pure function of its arguments -- no global state, no `random` -- so a
    rebuild is identical without anyone having to remember to reseed.
    """
    ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
    fx, fy, fz = _smooth(x - ix), _smooth(y - iy), _smooth(z - iz)
    c = [[[_hash01(ix + i, iy + j, iz + k, seed) for k in (0, 1)]
          for j in (0, 1)] for i in (0, 1)]
    lerp = lambda a, b, t: a + (b - a) * t
    x00 = lerp(c[0][0][0], c[1][0][0], fx)
    x10 = lerp(c[0][1][0], c[1][1][0], fx)
    x01 = lerp(c[0][0][1], c[1][0][1], fx)
    x11 = lerp(c[0][1][1], c[1][1][1], fx)
    return lerp(lerp(x00, x10, fy), lerp(x01, x11, fy), fz)


def fbm3(x, y, z, seed=0, octaves=4, lacunarity=2.0, gain=0.5):
    """Fractal sum of `noise3`, normalized back into [0, 1].

    **The output is nowhere near uniform on [0, 1].** It is a sum of smoothed
    noise, so it piles up in the middle: measured over a surface it runs about
    p20 = 0.40, p50 = 0.52, p80 = 0.62. Thresholds picked as if it were
    uniform therefore do not give the coverage they look like -- cuts at
    0.2/0.8 select far less than 20% each, and cuts chosen to *look* like a
    60/40 split hand most of the surface to one side. Two separate builds have
    been bitten by this.

    If the coverage matters, sample the field over the coordinates you are
    actually going to paint and take its quantiles:

        vals = [fbm3(x * s, y * s, z * s, seed) for x, y, z in coords]
        cuts = weighted_quantiles({v: 1 for v in vals}, [0.25, 0.75])

    That is the answer for buckets. When the field drives a *continuous*
    quantity instead -- a height, a depth, a tint strength -- `rank_normalize`
    is the same discipline in the other shape: it flattens the sample onto
    [0, 1] so `base + amp * rank` spends the whole range.
    """
    total, amp, norm, f = 0.0, 1.0, 0.0, 1.0
    for o in range(octaves):
        total += amp * noise3(x * f, y * f, z * f, seed + o * 101)
        norm += amp
        amp *= gain
        f *= lacunarity
    return total / norm


# --------------------------------------------------------------------------
# shapes -- every function returns a set of (x, y, z) integer coordinates
# --------------------------------------------------------------------------

class shapes:
    """Shape constructors. Combine with set operators.

    ``|`` union, ``-`` difference (carve), ``&`` intersection.

    Hollow variants are the filled shape minus an inset copy, so a wall of
    `thickness` is exact rather than approximated from the distance field.
    """

    @staticmethod
    def box(a, b, hollow=False, thickness=1):
        """Axis-aligned box spanning corners `a` and `b`, both inclusive."""
        (x0, x1), (y0, y1), (z0, z1) = (sorted(p) for p in zip(a, b))
        out = {(x, y, z)
               for x in range(x0, x1 + 1)
               for y in range(y0, y1 + 1)
               for z in range(z0, z1 + 1)}
        if hollow:
            t = thickness
            out -= shapes.box((x0 + t, y0 + t, z0 + t), (x1 - t, y1 - t, z1 - t))
        return out

    @staticmethod
    def sphere(center, radius, hollow=False, thickness=1):
        return shapes.ellipsoid(center, (radius, radius, radius),
                                hollow=hollow, thickness=thickness)

    @staticmethod
    def ellipsoid(center, radii, hollow=False, thickness=1):
        """Ellipsoid with per-axis `radii`, measured in voxels from center."""
        cx, cy, cz = center
        rx, ry, rz = radii
        out = set()
        for x in range(math.floor(cx - rx), math.ceil(cx + rx) + 1):
            for y in range(math.floor(cy - ry), math.ceil(cy + ry) + 1):
                for z in range(math.floor(cz - rz), math.ceil(cz + rz) + 1):
                    if (((x - cx) / (rx + 0.5)) ** 2
                            + ((y - cy) / (ry + 0.5)) ** 2
                            + ((z - cz) / (rz + 0.5)) ** 2) <= 1.0:
                        out.add((x, y, z))
        if hollow:
            inner = tuple(max(r - thickness, 0) for r in radii)
            out -= shapes.ellipsoid(center, inner)
        return out

    @staticmethod
    def cylinder(base, radius, height, axis="z", hollow=False, thickness=1):
        """Cylinder rising `height` voxels from `base` along `axis`.

        `base` is the center of the cap at the low end of `axis`.
        """
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        out = set()
        for h in range(height):
            for du in range(-int(radius) - 1, int(radius) + 2):
                for dv in range(-int(radius) - 1, int(radius) + 2):
                    if du * du + dv * dv <= (radius + 0.5) ** 2:
                        p = list(base)
                        p[ai] += h
                        p[u] += du
                        p[v] += dv
                        out.add(tuple(p))
        if hollow and radius > thickness:
            out -= shapes.cylinder(base, radius - thickness, height, axis=axis)
        return out

    @staticmethod
    def frustum(base, radius, height, top_radius=0.0, axis="z"):
        """Circular taper from `radius` at `base` to `top_radius` at the top.

        The general form of cylinder and cone: engine bells, tapered hull
        sections and gun barrels are all frusta.
        """
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        lim = int(max(radius, top_radius)) + 1
        out = set()
        for h in range(height):
            t = h / max(height - 1, 1)
            r = radius + (top_radius - radius) * t
            for du in range(-lim, lim + 1):
                for dv in range(-lim, lim + 1):
                    if du * du + dv * dv <= (r + 0.5) ** 2:
                        p = list(base)
                        p[ai] += h
                        p[u] += du
                        p[v] += dv
                        out.add(tuple(p))
        return out

    @staticmethod
    def cone(base, radius, height, axis="z", invert=False):
        """Cone tapering to a point `height` voxels above `base`.

        `invert=True` puts the point at `base` and the wide end at the top.
        """
        if invert:
            return shapes.frustum(base, 0.0, height, radius, axis=axis)
        return shapes.frustum(base, radius, height, 0.0, axis=axis)

    @staticmethod
    def pyramid(base, half_width, height, axis="z", invert=False):
        """Square-section pyramid; `half_width` is half the base edge."""
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        out = set()
        for h in range(height):
            t = h / max(height - 1, 1)
            w = int(round(half_width * (t if invert else 1.0 - t)))
            for du in range(-w, w + 1):
                for dv in range(-w, w + 1):
                    p = list(base)
                    p[ai] += h
                    p[u] += du
                    p[v] += dv
                    out.add(tuple(p))
        return out

    @staticmethod
    def torus(center, major, minor, axis="z"):
        """Ring of tube radius `minor` whose centerline has radius `major`."""
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        lim = int(major + minor) + 1
        out = set()
        for du in range(-lim, lim + 1):
            for dv in range(-lim, lim + 1):
                ring = math.hypot(du, dv) - major
                for dw in range(-int(minor) - 1, int(minor) + 2):
                    if ring * ring + dw * dw <= (minor + 0.5) ** 2:
                        p = list(center)
                        p[ai] += dw
                        p[u] += du
                        p[v] += dv
                        out.add(tuple(p))
        return out

    @staticmethod
    def line(a, b, thickness=1):
        """Voxelized segment from `a` to `b`; `thickness` is a diameter."""
        steps = max(abs(b[i] - a[i]) for i in range(3)) or 1
        r = (thickness - 1) / 2.0
        out = set()
        for s in range(steps + 1):
            t = s / steps
            p = [a[i] + (b[i] - a[i]) * t for i in range(3)]
            if thickness <= 1:
                out.add(tuple(int(round(v)) for v in p))
            else:
                out |= shapes.ellipsoid(tuple(int(round(v)) for v in p),
                                        (r, r, r))
        return out

    @staticmethod
    def wedge(a, b, axis="x", taper="z", invert=False):
        """Right-triangular prism filling the box `a`..`b`.

        The extent along `taper` is full at the low end of `axis` and shrinks
        to a single layer at the high end; `invert=True` swaps the ends. This
        is the ramp/nose/swept-wing primitive -- the thin end keeps one layer
        rather than vanishing, which is what you want in voxels.
        """
        ai, ti = "xyz".index(axis), "xyz".index(taper)
        if ai == ti:
            raise ValueError("wedge axis and taper must differ")
        wi = 3 - ai - ti
        lo, hi = zip(*(sorted(p) for p in zip(a, b)))
        out = set()
        span = hi[ai] - lo[ai]
        for i in range(lo[ai], hi[ai] + 1):
            t = (i - lo[ai]) / span if span else 0.0
            f = t if invert else 1.0 - t
            top = lo[ti] + int(round(f * (hi[ti] - lo[ti])))
            for j in range(lo[ti], top + 1):
                for k in range(lo[wi], hi[wi] + 1):
                    p = [0, 0, 0]
                    p[ai], p[ti], p[wi] = i, j, k
                    out.add(tuple(p))
        return out

    @staticmethod
    def polygon(points, offset, height, axis="z"):
        """Extrude a closed 2D polygon `height` voxels along `axis`.

        `points` are (u, v) pairs in the two axes other than `axis`, in xyz
        order -- (x, y) for axis="z", (x, z) for "y", (y, z) for "x". They are
        absolute coordinates in those axes; `offset` is where the extrusion
        starts along `axis`. Fill is even-odd, and the outline is always
        included, so a hand-drawn silhouette never loses its edge.

        Drawing a ship's plan view as a polygon and extruding it beats
        stacking boxes for anything with a swept or angled profile.
        """
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        pts = [(int(p[0]), int(p[1])) for p in points]
        if len(pts) < 3:
            raise ValueError("a polygon needs at least 3 points")

        flat = set()
        for i, (au, av) in enumerate(pts):        # outline
            bu, bv = pts[(i + 1) % len(pts)]
            steps = max(abs(bu - au), abs(bv - av)) or 1
            for s in range(steps + 1):
                t = s / steps
                flat.add((round(au + (bu - au) * t), round(av + (bv - av) * t)))

        lo_u, hi_u = min(p[0] for p in pts), max(p[0] for p in pts)
        lo_v, hi_v = min(p[1] for p in pts), max(p[1] for p in pts)
        for pu in range(lo_u, hi_u + 1):          # even-odd fill
            for pv in range(lo_v, hi_v + 1):
                inside = False
                for i, (au, av) in enumerate(pts):
                    bu, bv = pts[(i + 1) % len(pts)]
                    if (av > pv) != (bv > pv):
                        cross = au + (pv - av) * (bu - au) / (bv - av)
                        if pu < cross:
                            inside = not inside
                if inside:
                    flat.add((pu, pv))

        out = set()
        for h in range(height):
            for pu, pv in flat:
                p = [0, 0, 0]
                p[ai], p[u], p[v] = offset + h, pu, pv
                out.add(tuple(p))
        return out

    @staticmethod
    def helix(base, radius, height, turns=1.0, axis="z", thickness=1,
              phase=0.0):
        """Coil of `turns` revolutions rising `height` voxels from `base`.

        `base` is the center of the low end, as for cylinder. `thickness` is
        the wire diameter. Coils, springs and the windings on a tesla node.
        """
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        r = (thickness - 1) / 2.0
        steps = max(int(abs(turns) * max(radius, 1) * 8), height, 1)
        out = set()
        for s in range(steps + 1):
            t = s / steps
            ang = phase + 2 * math.pi * turns * t
            p = list(base)
            p[ai] += int(round(t * (height - 1)))
            p[u] += int(round(radius * math.cos(ang)))
            p[v] += int(round(radius * math.sin(ang)))
            if thickness <= 1:
                out.add(tuple(p))
            else:
                out |= shapes.ellipsoid(tuple(p), (r, r, r))
        return out

    @staticmethod
    def _mask_cells(mask, view):
        """Parse one silhouette mask into (cells, width, height).

        Cells are (column, row) with row 0 at the *bottom*, matching the way
        preview() prints the highest row first.
        """
        rows = mask.splitlines() if isinstance(mask, str) else list(mask)
        # Trim only the blank lines triple-quoting adds at the ends; a row of
        # dots is a real empty row.
        while rows and not rows[0].strip():
            rows.pop(0)
        while rows and not rows[-1].strip():
            rows.pop()
        if not rows:
            raise ValueError(f"the {view} mask has no rows")
        height = len(rows)
        width = max(len(r) for r in rows)
        cells = {(col, height - 1 - i)
                 for i, row in enumerate(rows)
                 for col, ch in enumerate(row) if ch not in ". "}
        return cells, width, height

    @staticmethod
    def silhouette_hull(front=None, side=None, top=None):
        """Voxels whose projections match every given silhouette mask.

        The visual hull of two or three orthographic drawings: draw what the
        thing looks like from the front, the side and above, and keep the
        solid that casts all of those shadows. Masks read exactly like
        `preview()` output -- front is x right and z up, side is y right and
        z up, top is x right and +y at the top -- so a drawing and a preview
        of the result are directly comparable.

        Each mask is a list of rows or one multiline string. '.' and ' ' are
        empty, every other character is filled, and short rows pad on the
        right. The hull is anchored at the origin: the leftmost column is 0,
        the bottom text row is 0.

        Two masks are the minimum; a single one leaves an axis unbounded.
        Views that define the same extent must agree on it.
        """
        masks = {}
        for view, mask in (("front", front), ("side", side), ("top", top)):
            if mask is not None:
                masks[view] = shapes._mask_cells(mask, view)
        if len(masks) < 2:
            raise ValueError(
                "silhouette_hull needs at least two masks; one on its own "
                "leaves the third axis unbounded"
            )

        # (view, what it measures, size) for every view that pins an axis.
        for axis, claims in (
                ("x", [("front", "columns", 1), ("top", "columns", 1)]),
                ("y", [("side", "columns", 1), ("top", "rows", 2)]),
                ("z", [("front", "rows", 2), ("side", "rows", 2)])):
            claims = [(v, what, masks[v][i]) for v, what, i in claims
                      if v in masks]
            for v, what, n in claims[1:]:
                v0, what0, n0 = claims[0]
                if n != n0:
                    raise ValueError(f"{v0} is {n0} {what0} ({axis}) but "
                                     f"{v} is {n} {what}")

        f = masks.get("front")     # cells are (x, z)
        s = masks.get("side")      # cells are (y, z)
        t = masks.get("top")       # cells are (x, y)

        out = set()
        if f is not None and s is not None:
            by_z = {}
            for y, z in s[0]:
                by_z.setdefault(z, []).append(y)
            for x, z in f[0]:
                for y in by_z.get(z, ()):
                    if t is None or (x, y) in t[0]:
                        out.add((x, y, z))
        elif f is not None:                     # front + top
            by_x = {}
            for x, y in t[0]:
                by_x.setdefault(x, []).append(y)
            for x, z in f[0]:
                for y in by_x.get(x, ()):
                    out.add((x, y, z))
        else:                                   # side + top
            by_y = {}
            for x, y in t[0]:
                by_y.setdefault(y, []).append(x)
            for y, z in s[0]:
                for x in by_y.get(y, ()):
                    out.add((x, y, z))
        return out

    @staticmethod
    def rock(center, radii, seed=0, roughness=0.30, freq=1.7, floor=0.60):
        """A lumpy triaxial solid: an ellipsoid chewed up by 3-D value noise.

        The primitive for anything that is not supposed to look manufactured
        -- boulders, rubble, asteroids, a potato, a lump of ore. Every other
        shape here is smooth, and a scatter of small spheres standing in for
        rough reads as bad anti-aliasing rather than as a rough thing.

        `radii` is (rx, ry, rz) of the underlying ellipsoid, so shape is
        available as an input rather than just size -- most real irregular
        objects are markedly non-round, and a 3:1 lump and a 1:1 lump read as
        different objects, not as the same object at two sizes. The noise is
        sampled on the ellipsoid's *own* unit sphere, so the lumps stay smooth
        and evenly sized however squashed it is.

        `roughness` is the fraction of the radius the surface may wander by,
        `floor` the smallest fraction it may shrink to, and `freq` how many
        lumps go round it. The result is filtered to its largest connected
        component: at radii near a single voxel the noise can otherwise shear
        a corner clean off, which then shows up as a phantom extra object in
        any component count.
        """
        cx, cy, cz = center
        rx, ry, rz = (max(0.6, float(v)) for v in radii)
        lim = [int(math.ceil(v * (1.0 + 2.0 * roughness))) + 1
               for v in (rx, ry, rz)]
        out = set()
        for dz in range(-lim[2], lim[2] + 1):
            for dy in range(-lim[1], lim[1] + 1):
                for dx in range(-lim[0], lim[0] + 1):
                    q = math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2
                                  + (dz / rz) ** 2)
                    if q == 0.0:
                        out.add((cx, cy, cz))
                        continue
                    if q > 1.0 + 2.0 * roughness:
                        continue
                    ux, uy, uz = dx / (rx * q), dy / (ry * q), dz / (rz * q)
                    k = fbm3(ux * freq, uy * freq, uz * freq, seed, octaves=3)
                    if q <= max(floor, 1.0 + roughness * (2.0 * k - 1.0) * 2.0):
                        out.add((cx + dx, cy + dy, cz + dz))
        parts = components(out)
        return parts[0] if parts else {(cx, cy, cz)}

    @staticmethod
    def _walk6(a, b):
        """Integer path from `a` to `b` inclusive, stepping one axis at a time.

        Face-connected by construction, which `line` is not: `line` rounds a
        parametric sample per step and so happily produces two voxels that
        meet only at a corner.
        """
        cur = list(a)
        d = [b[i] - a[i] for i in range(3)]
        n = sum(abs(v) for v in d)
        out = [tuple(cur)]
        if n == 0:
            return out
        step = [1 if v > 0 else -1 for v in d]
        done = [0, 0, 0]
        for k in range(1, n + 1):
            t = k / n
            pick, worst = -1, -1.0
            for i in range(3):
                if done[i] >= abs(d[i]):
                    continue
                short = abs(a[i] + d[i] * t - cur[i])
                if short > worst:
                    worst, pick = short, i
            cur[pick] += step[pick]
            done[pick] += 1
            out.append(tuple(cur))
        return out

    @staticmethod
    def tube(points, radius, end_radius=None, taper=1.0):
        """A ball swept along the polyline `points`, optionally tapering.

        One connected piece for any radius, including 0 -- the spine is walked
        one axis step at a time rather than sampled, so no two consecutive
        stamps can meet at a corner and no gap can open however sharply the
        path turns or however coarsely `points` samples it. That is the whole
        reason to reach for this over `line`: the connectivity is by
        construction rather than something you check afterwards and hope.

        `points` may be floats; they are rounded to the lattice. `radius` is
        the radius at the first point and `end_radius` at the last, defaulting
        to no taper, interpolated over the spine and raised to `taper` (> 1
        keeps the tube fat and closes late, < 1 pinches early).

        Connecting to something else is a separate question, and the trick is
        the same one: run the endpoints *inside* the other object rather than
        onto its surface. A path that starts within a shell's inner boundary
        and passes outside its outer boundary must put a voxel in the shell,
        because the spine advances one voxel at a time. An endpoint placed on
        the surface can miss it by one and float.
        """
        pts = [tuple(int(round(v)) for v in p) for p in points]
        if len(pts) < 2:
            raise ValueError("a tube needs at least two points")
        spine = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            spine.extend(shapes._walk6(a, b)[1:])

        n = max(len(spine) - 1, 1)
        r1 = radius if end_radius is None else end_radius
        balls = {}
        out = set()
        for i, (x, y, z) in enumerate(spine):
            r = round(radius + (r1 - radius) * (i / n) ** taper, 3)
            ball = balls.get(r)
            if ball is None:
                ball = balls[r] = shapes.ellipsoid((0, 0, 0), (r, r, r))
            out |= {(x + dx, y + dy, z + dz) for dx, dy, dz in ball}
        return out

    @staticmethod
    def where(a, b, predicate):
        """Every coordinate in the box `a`..`b` for which predicate(x,y,z) is true.

        The escape hatch for anything the named primitives do not cover.
        """
        return {(x, y, z) for (x, y, z) in shapes.box(a, b)
                if predicate(x, y, z)}


# --------------------------------------------------------------------------
# transforms on coordinate sets
# --------------------------------------------------------------------------

def translate(coords, offset):
    dx, dy, dz = offset
    return {(x + dx, y + dy, z + dz) for x, y, z in coords}


def mirror(coords, axis="x", at=0):
    """Reflect across the plane `axis = at`. Returns only the reflection."""
    ai = "xyz".index(axis)
    out = set()
    for c in coords:
        p = list(c)
        p[ai] = 2 * at - p[ai]
        out.add(tuple(p))
    return out


def rotate90(coords, axis="z", turns=1):
    """Rotate a multiple of 90 degrees about `axis` through the origin."""
    ai = "xyz".index(axis)
    u, v = [i for i in range(3) if i != ai]
    out = set(coords)
    for _ in range(turns % 4):
        rotated = set()
        for c in out:
            p = list(c)
            p[u], p[v] = c[v], -c[u]
            rotated.add(tuple(p))
        out = rotated
    return out


def scale(coords, factor):
    """Blow up by an integer factor, each voxel becoming a factor^3 cube."""
    f = int(factor)
    return {(x * f + i, y * f + j, z * f + k)
            for x, y, z in coords
            for i in range(f) for j in range(f) for k in range(f)}


def bounds(coords):
    """(min_corner, max_corner) of a coordinate set, or None if empty."""
    if not coords:
        return None
    xs, ys, zs = zip(*coords)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _flood(coords, seed):
    """Face-adjacent flood fill over `coords`, starting at `seed`."""
    seen = {seed}
    stack = [seed]
    while stack:
        x, y, z = stack.pop()
        for n in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z),
                  (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)):
            if n in coords and n not in seen:
                seen.add(n)
                stack.append(n)
    return seen


def components(coords):
    """Face-connected components of a coordinate set, largest first.

    The coordinate-set twin of `Model.components`, for checking a shape before
    it is ever painted -- which is where a generated shape that came apart is
    cheapest to catch.
    """
    remaining = set(coords)
    out = []
    while remaining:
        part = _flood(remaining, next(iter(remaining)))
        out.append(part)
        remaining -= part
    return sorted(out, key=len, reverse=True)


def distance_field(seeds, domain):
    """Steps from the nearest seed cell to every reachable cell of `domain`.

    Breadth-first over axis neighbours, so cells work in any dimension: 2D
    `(x, y)` columns for a footprint, 3D `(x, y, z)` for a solid. Returns
    `{cell: steps}` with `steps >= 1`. Seeds are not in the result, and cells
    of `domain` that no path reaches are absent -- `dist.get(c, default)` is
    the honest way to read it.

    This is how a region gets a *gradient away from something*: a bank whose
    height rises with its distance from the water, moss thinning away from a
    wall, a tint that fades inland. Distance to the boundary of a region is
    the same call with the region as the domain and everything outside it as
    the seeds.

        land = all_columns - water
        dist = distance_field(water, land)
        height = {c: min(MAX_H, SLOPE * dist.get(c, 1)) for c in land}

    Steps are Manhattan-along-the-domain, not straight-line: a cell reachable
    only the long way round an obstacle is correctly far away, which is the
    point of doing it as a fill rather than as arithmetic.
    """
    seeds = set(seeds)
    todo = set(domain) - seeds
    dist = {}
    frontier = list(seeds)
    d = 0
    while frontier and todo:
        d += 1
        nxt = []
        for cell in frontier:
            for i in range(len(cell)):
                for step in (1, -1):
                    n = cell[:i] + (cell[i] + step,) + cell[i + 1:]
                    if n in todo:
                        todo.discard(n)
                        dist[n] = d
                        nxt.append(n)
        frontier = nxt
    return dist


def _unit(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n == 0:
        raise ValueError("cannot normalize a zero vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


# --------------------------------------------------------------------------
# png rendering -- see Model.render
# --------------------------------------------------------------------------

#: One entry per cube face: outward normal, the four corners of the face as
#: offsets from the voxel's minimum corner (in traversal order, so the quad is
#: not self-crossing), and the two tangent axes the ambient-occlusion probe
#: steps along.
_FACES = (
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
     ((0, 1, 0), (0, 0, 1))),
    ((-1, 0, 0), ((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)),
     ((0, 1, 0), (0, 0, 1))),
    ((0, 1, 0), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)),
     ((1, 0, 0), (0, 0, 1))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
     ((1, 0, 0), (0, 0, 1))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
     ((1, 0, 0), (0, 1, 0))),
    ((0, 0, -1), ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
     ((1, 0, 0), (0, 1, 0))),
)

_AMBIENT = 0.35          # light a face gets with the lamp edge-on
_FILL = 0.15             # low fill opposite the key's azimuth; see render()
_AO_FACTOR = 0.92        # darkening per filled cell beside an exposed face
_MIN_CANVAS = 32         # px, so an edge-on flat model is not a sliver


def _write_png(width, height, rgb):
    """Encode raw RGB bytes as a PNG. Filter 0 on every row, one IDAT."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    stride = width * 3
    raw = bytearray()
    for r in range(height):
        raw.append(0)                       # filter type 0 (None)
        raw += rgb[r * stride:(r + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR",
                    struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def _fill_quad(fb, w, h, pts, pat):
    """Scanline-fill a convex quad into a w x h RGB framebuffer.

    `pts` are four (x, y) pixel-space points and `pat` is the 3-byte color.
    A pixel is filled when its center lies inside the quad, which is what
    makes two faces sharing an edge tile without a seam or an overdraw.
    """
    ys = (pts[0][1], pts[1][1], pts[2][1], pts[3][1])
    r0 = max(0, math.ceil(min(ys) - 0.5))
    r1 = min(h - 1, math.floor(max(ys) - 0.5))
    for r in range(r0, r1 + 1):
        yc = r + 0.5
        lo = hi = None
        xa, ya = pts[3]
        for xb, yb in pts:
            if (ya <= yc) != (yb <= yc):
                xi = xa + (xb - xa) * (yc - ya) / (yb - ya)
                if lo is None or xi < lo:
                    lo = xi
                if hi is None or xi > hi:
                    hi = xi
            xa, ya = xb, yb
        if lo is None:
            continue
        c0 = max(0, math.ceil(lo - 0.5))
        c1 = min(w - 1, math.floor(hi - 0.5))
        if c1 >= c0:
            p = (r * w + c0) * 3
            fb[p:p + (c1 - c0 + 1) * 3] = pat * (c1 - c0 + 1)


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

class Model:
    """A sparse voxel model: coordinates mapped to palette indices."""

    def __init__(self, palette=None):
        self.voxels = {}
        self.palette = palette or Palette()

    # -- primitive placement ------------------------------------------------
    # Each wrapper mirrors the matching shapes.* signature and paints the
    # result, since "make a shape and color it" is the overwhelmingly common
    # case; drop to shapes.* directly when you need set algebra first.

    def box(self, a, b, color, **kw):
        return self.add(shapes.box(a, b, **kw), color)

    def sphere(self, center, radius, color, **kw):
        return self.add(shapes.sphere(center, radius, **kw), color)

    def ellipsoid(self, center, radii, color, **kw):
        return self.add(shapes.ellipsoid(center, radii, **kw), color)

    def cylinder(self, base, radius, height, color, **kw):
        return self.add(shapes.cylinder(base, radius, height, **kw), color)

    def cone(self, base, radius, height, color, **kw):
        return self.add(shapes.cone(base, radius, height, **kw), color)

    def frustum(self, base, radius, height, color, **kw):
        return self.add(shapes.frustum(base, radius, height, **kw), color)

    def wedge(self, a, b, color, **kw):
        return self.add(shapes.wedge(a, b, **kw), color)

    def polygon(self, points, offset, height, color, **kw):
        return self.add(shapes.polygon(points, offset, height, **kw), color)

    def helix(self, base, radius, height, color, **kw):
        return self.add(shapes.helix(base, radius, height, **kw), color)

    def pyramid(self, base, half_width, height, color, **kw):
        return self.add(shapes.pyramid(base, half_width, height, **kw), color)

    def torus(self, center, major, minor, color, **kw):
        return self.add(shapes.torus(center, major, minor, **kw), color)

    def line(self, a, b, color, **kw):
        return self.add(shapes.line(a, b, **kw), color)

    def rock(self, center, radii, color, **kw):
        return self.add(shapes.rock(center, radii, **kw), color)

    def tube(self, points, radius, color, **kw):
        return self.add(shapes.tube(points, radius, **kw), color)

    def voxel(self, pos, color):
        return self.add({tuple(pos)}, color)

    # -- core edits ---------------------------------------------------------

    def add(self, coords, color):
        """Paint `coords` with `color`, overwriting whatever was there."""
        idx = self.palette.index(color)
        for c in coords:
            self.voxels[tuple(c)] = idx
        return self

    def add_under(self, coords, color):
        """Paint only where nothing has been placed yet."""
        idx = self.palette.index(color)
        for c in coords:
            self.voxels.setdefault(tuple(c), idx)
        return self

    def remove(self, coords):
        """Carve `coords` away."""
        for c in coords:
            self.voxels.pop(tuple(c), None)
        return self

    def keep(self, coords):
        """Intersect the model with `coords`, deleting everything outside."""
        keep = {tuple(c) for c in coords}
        self.voxels = {k: v for k, v in self.voxels.items() if k in keep}
        return self

    def merge(self, other, offset=(0, 0, 0), under=False):
        """Stamp another model into this one at `offset`, remapping colors."""
        for pos, idx in other.voxels.items():
            dst = (pos[0] + offset[0], pos[1] + offset[1], pos[2] + offset[2])
            mine = self.palette.index(other.palette.rgba(idx))
            if under:
                self.voxels.setdefault(dst, mine)
            else:
                self.voxels[dst] = mine
        return self

    def mirror(self, axis="x", at=0):
        """Reflect the model across `axis = at` and keep both halves.

        The workhorse for anything bilaterally symmetric: build one side,
        call this, done.
        """
        ai = "xyz".index(axis)
        for pos, idx in list(self.voxels.items()):
            p = list(pos)
            p[ai] = 2 * at - p[ai]
            self.voxels.setdefault(tuple(p), idx)
        return self

    def translate(self, offset):
        dx, dy, dz = offset
        self.voxels = {(x + dx, y + dy, z + dz): i
                       for (x, y, z), i in self.voxels.items()}
        return self

    def rotate90(self, axis="z", turns=1):
        """Rotate the whole model about `axis` through the origin.

        Colors ride along, unlike the coordinate-set `rotate90`. Build a part
        once pointing one way, then orient copies of it.
        """
        ai = "xyz".index(axis)
        u, v = [i for i in range(3) if i != ai]
        for _ in range(turns % 4):
            rotated = {}
            for pos, idx in self.voxels.items():
                p = list(pos)
                p[u], p[v] = pos[v], -pos[u]
                rotated[tuple(p)] = idx
            self.voxels = rotated
        return self

    def scale(self, factor):
        """Blow up by an integer factor, each voxel becoming a factor^3 cube."""
        f = int(factor)
        self.voxels = {(x * f + i, y * f + j, z * f + k): idx
                       for (x, y, z), idx in self.voxels.items()
                       for i in range(f) for j in range(f) for k in range(f)}
        return self

    def recolor(self, old, new):
        """Repaint every voxel of color `old` as `new`. Returns the count.

        The cheap way to reskin a shared model -- a hull in faction colors,
        an enemy tinted for its elite variant.
        """
        src = self.palette.index(old)
        dst = self.palette.index(new)
        n = 0
        for pos, idx in self.voxels.items():
            if idx == src:
                self.voxels[pos] = dst
                n += 1
        return n

    def center(self, axes="xy"):
        """Translate so the bounding box is centered on the origin in `axes`.

        Defaults to the horizontal axes, since a model usually wants to keep
        sitting on z=0 while being centered left-right and front-back.
        """
        lo, hi = self._extent()
        off = [0, 0, 0]
        for ch in axes:
            i = "xyz".index(ch)
            off[i] = -((lo[i] + hi[i]) // 2)
        return self.translate(tuple(off))

    def copy(self):
        m = Model(self.palette)
        m.voxels = dict(self.voxels)
        return m

    # -- queries ------------------------------------------------------------

    def __len__(self):
        return len(self.voxels)

    def __contains__(self, pos):
        return tuple(pos) in self.voxels

    def coords(self):
        return set(self.voxels)

    @property
    def bounds(self):
        """(min_corner, max_corner), or None if the model is empty."""
        return self._extent() if self.voxels else None

    def _extent(self):
        """Like `bounds`, but raising on empty, for callers that need both."""
        if not self.voxels:
            raise ValueError("model is empty")
        xs, ys, zs = zip(*self.voxels)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    @property
    def size(self):
        """Extent in voxels along each axis."""
        b = self.bounds
        if b is None:
            return (0, 0, 0)
        lo, hi = b
        return tuple(hi[i] - lo[i] + 1 for i in range(3))

    def stats(self):
        """One-line summary: voxel count, size, bounds, colors used."""
        if not self.voxels:
            return "empty model"
        lo, hi = self._extent()
        used = len(set(self.voxels.values()))
        return (f"{len(self.voxels)} voxels  size={self.size}  "
                f"bounds={tuple(lo)}..{tuple(hi)}  colors={used}")

    def color_histogram(self):
        """Voxel count per color name, most common first."""
        counts = Counter(self.voxels.values())
        return [(self.palette.name(i), n) for i, n in counts.most_common()]

    def surface(self, facing=None):
        """Voxels with an exposed face, optionally only those facing one way.

        `facing` is "x+", "x-", "y+", "y-", "z+" or "z-". Painting
        `m.surface("z-")` a darker color gives an underside shadow; a whole
        surface repaint gives hull plating over a solid interior.
        """
        if facing is None:
            dirs = [(1, 0, 0), (-1, 0, 0), (0, 1, 0),
                    (0, -1, 0), (0, 0, 1), (0, 0, -1)]
        else:
            if len(facing) != 2 or facing[0] not in "xyz" or facing[1] not in "+-":
                raise ValueError(f"bad facing {facing!r}; want e.g. 'z+'")
            d = [0, 0, 0]
            d["xyz".index(facing[0])] = 1 if facing[1] == "+" else -1
            dirs = [tuple(d)]
        return {(x, y, z) for (x, y, z) in self.voxels
                if any((x + dx, y + dy, z + dz) not in self.voxels
                       for dx, dy, dz in dirs)}

    def support(self, coords, offset=(0, 0, -1)):
        """(supported, total) for `coords`: how many have a filled neighbor.

        The check `detached()` cannot make. A part can be connected to the
        model by a single stray voxel and still be resting on nothing -- pass
        its bottom layer here and compare the two numbers.
        """
        dx, dy, dz = offset
        total = 0
        n = 0
        for x, y, z in coords:
            total += 1
            if (x + dx, y + dy, z + dz) in self.voxels:
                n += 1
        return n, total

    # -- connectivity -------------------------------------------------------
    # Assembled models tend to fail by having a part float a voxel away from
    # what it should rest on. That is invisible in the voxel count and easy to
    # miss in a projection, but obvious as a connectivity break.

    def _flood(self, seed):
        """Face-adjacent flood fill over filled voxels, starting at `seed`."""
        return _flood(self.voxels, seed)

    def detached(self, seed=None):
        """Voxels not reachable from `seed`; empty means one solid piece.

        `seed` defaults to the lowest voxel in the model, which for anything
        resting on a base or plate is the part everything should hang off.
        Adjacency is face-only, so parts that meet just at an edge or corner
        count as detached.
        """
        if not self.voxels:
            return set()
        if seed is None:
            seed = min(self.voxels, key=lambda c: (c[2], c[0], c[1]))
        seed = tuple(seed)
        if seed not in self.voxels:
            raise ValueError(f"seed {seed} is not a filled voxel")
        return set(self.voxels) - self._flood(seed)

    def components(self):
        """Connected components as sets of coordinates, largest first."""
        return components(self.voxels)

    def radial_profile(self, center, direction, normal, r_lo, r_hi,
                       step=1.0, search=3):
        """Colors met walking outward from `center`, as [(radius, name), ...].

        The check a voxel count cannot make, for anything built in radial
        layers -- a ring system, a tree stump, a dartboard, a gasket, a
        layered cake. "31,000 voxels" is equally true of a ring system with
        its gap in the wrong place, its bands in the wrong order, or its gap
        painted black; a radial color walk is false of all three.

        `direction` is the in-plane direction to walk and `normal` is the
        plane's normal; both may be any length. Each radius is probed up to
        `search` voxels along the normal, because a plane that is not
        axis-aligned lands between lattice points and the voxel that was
        actually written is a step off the ideal one. The probe works outward
        from the plane and takes the nearest hit, so a thick disc reports the
        color at the radius asked for rather than whatever the far face of the
        search window happened to hold. `None` means nothing was in range.
        """
        d = _unit(direction)
        n = _unit(normal)
        offsets = [0]
        for k in range(1, search + 1):
            offsets += [-k, k]
        out = []
        r = r_lo
        while r <= r_hi + 1e-9:
            found = None
            for k in offsets:
                p = tuple(int(round(center[i] + r * d[i] + k * n[i]))
                          for i in range(3))
                if p in self.voxels:
                    found = self.palette.name(self.voxels[p])
                    break
            out.append((r, found))
            r += step
        return out

    # -- io -----------------------------------------------------------------

    def save(self, path, origin=None):
        """Write a .vox file. Returns a summary string.

        The model is shifted so its minimum corner lands on `origin`
        (default: the file origin), which is what lets you build around
        negative coordinates.
        """
        if not self.voxels:
            raise ValueError("refusing to save an empty model")
        lo, _ = self._extent()
        ox, oy, oz = origin or (0, 0, 0)
        shift = (ox - lo[0], oy - lo[1], oz - lo[2])
        placed = [(x + shift[0], y + shift[1], z + shift[2], i)
                  for (x, y, z), i in self.voxels.items()]

        size = tuple(max(v[i] for v in placed) + 1 for i in range(3))
        for axis, n in zip("xyz", size):
            if n > MAX_DIM:
                raise ValueError(
                    f"model is {size} voxels, but .vox allows at most "
                    f"{MAX_DIM} per axis ({axis} is {n}). Split it into "
                    "several models or scale it down."
                )
        if any(v[i] < 0 for v in placed for i in range(3)):
            raise ValueError(f"origin {origin} pushes voxels below zero")

        body = (_chunk(b"SIZE", struct.pack("<iii", *size))
                + _chunk(b"XYZI", struct.pack("<i", len(placed))
                         + b"".join(bytes(v) for v in placed))
                + _chunk(b"RGBA", self.palette.chunk_bytes()))
        data = b"VOX " + struct.pack("<i", 150) + _chunk(b"MAIN", b"", body)
        with open(path, "wb") as f:
            f.write(data)
        return (f"wrote {path}: {len(placed)} voxels, size {size}, "
                f"{len(self.palette)} colors, {len(data)} bytes")

    @classmethod
    def load(cls, path):
        """Read a .vox file into one flat model. Handles multi-model files.

        Multi-model files are flattened, honoring the scene graph: each
        nSHP's model is offset by the nTRN translations accumulated on the
        way down to it. A file with no scene graph is read at face value,
        which is our own single-model output and most other writers' too.

        This is what round-trips `Scene.save` back into world coordinates.

        Rotations (`_r`) are ignored. That is free for our own files, which
        never write one, but foreign files lean on them heavily -- expect a
        rotated model to land in the right place facing the wrong way. Note
        that identity is `_r` absent or "4", not 0.
        """
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"VOX ":
            raise ValueError(f"{path} is not a .vox file")

        model = cls()
        rgba = None
        sizes = []
        xyzi_chunks = []
        transforms = {}      # nTRN node id -> (child node id, translation)
        groups = {}          # nGRP node id -> [child node ids]
        shape_nodes = {}     # nSHP node id -> model index
        for cid, content in _walk_chunks(data, 8):
            if cid == b"SIZE":
                sizes.append(struct.unpack_from("<iii", content, 0))
            elif cid == b"XYZI":
                n = struct.unpack_from("<i", content, 0)[0]
                xyzi_chunks.append([tuple(content[4 + i * 4:8 + i * 4])
                                    for i in range(n)])
            elif cid == b"RGBA":
                rgba = [tuple(content[i * 4:i * 4 + 4]) for i in range(256)]
            elif cid == b"nTRN":
                node = struct.unpack_from("<i", content, 0)[0]
                _, off = _read_dict(content, 4)
                child = struct.unpack_from("<i", content, off)[0]
                frames = struct.unpack_from("<i", content, off + 12)[0]
                off += 16
                shift = (0, 0, 0)
                for i in range(frames):
                    frame, off = _read_dict(content, off)
                    parts = frame.get("_t", "").split()
                    if i == 0 and len(parts) == 3:
                        shift = tuple(int(v) for v in parts)
                transforms[node] = (child, shift)
            elif cid == b"nGRP":
                node = struct.unpack_from("<i", content, 0)[0]
                _, off = _read_dict(content, 4)
                n = struct.unpack_from("<i", content, off)[0]
                groups[node] = list(
                    struct.unpack_from(f"<{n}i", content, off + 4))
            elif cid == b"nSHP":
                node = struct.unpack_from("<i", content, 0)[0]
                _, off = _read_dict(content, 4)
                if struct.unpack_from("<i", content, off)[0] >= 1:
                    shape_nodes[node] = struct.unpack_from("<i", content,
                                                           off + 4)[0]

        # (model index, world position of that model's local origin). The
        # graph stores each transform as the position of the model's
        # *center*, so the minimum corner sits at translation - size // 2.
        placements = []
        if transforms:
            stack = [(0, (0, 0, 0))]
            while stack:
                node, at = stack.pop()
                if node in transforms:
                    child, (dx, dy, dz) = transforms[node]
                    stack.append((child, (at[0] + dx, at[1] + dy, at[2] + dz)))
                elif node in groups:
                    stack.extend((child, at) for child in groups[node])
                elif node in shape_nodes:
                    mid = shape_nodes[node]
                    size = sizes[mid] if mid < len(sizes) else (0, 0, 0)
                    placements.append(
                        (mid, tuple(at[i] - size[i] // 2 for i in range(3))))
            placements.sort()    # model order, so overwrites are deterministic
        else:
            placements = [(i, (0, 0, 0)) for i in range(len(xyzi_chunks))]

        remap = {}
        for mid, (ox, oy, oz) in placements:
            if mid >= len(xyzi_chunks):
                continue
            for x, y, z, idx in xyzi_chunks[mid]:
                if idx not in remap:
                    remap[idx] = (model.palette.index(rgba[idx - 1])
                                  if rgba else model.palette.index(idx))
                model.voxels[(x + ox, y + oy, z + oz)] = remap[idx]
        return model

    # -- preview ------------------------------------------------------------

    def preview(self, max_dim=48, ansi=False, views=("front", "side", "top")):
        """Orthographic ASCII projections, for checking shapes in a terminal.

        front: looking along +Y (x right, z up).  side: along -X (y right,
        z up).  top: looking down (x right, y up, +Y at the top of the block).
        Pass ansi=True for truecolor blocks.
        """
        if not self.voxels:
            return "empty model"
        lo = self._extent()[0]
        span = max(self.size) or 1
        step = max(1, math.ceil(span / max_dim))

        chars = "#*+=o%&$xX~-:."
        legend, blocks = {}, []
        for view in views:
            grid = {}
            for (x, y, z), idx in self.voxels.items():
                # (column, row, depth) per view; smaller depth draws on top
                if view == "front":
                    key, depth = (x - lo[0], z - lo[2]), y
                elif view == "side":
                    key, depth = (y - lo[1], z - lo[2]), -x
                else:
                    key, depth = (x - lo[0], y - lo[1]), -z
                cell = (key[0] // step, key[1] // step)
                if cell not in grid or depth < grid[cell][0]:
                    grid[cell] = (depth, idx)

            cols = max(c for c, _ in grid) + 1
            rows = max(r for _, r in grid) + 1
            lines = []
            for r in reversed(range(rows)):
                line = []
                for c in range(cols):
                    hit = grid.get((c, r))
                    if hit is None:
                        line.append("  " if ansi else " ")
                    elif ansi:
                        rr, gg, bb, _ = self.palette.rgba(hit[1])
                        line.append(f"\x1b[38;2;{rr};{gg};{bb}m##\x1b[0m")
                    else:
                        idx = hit[1]
                        if idx not in legend:
                            legend[idx] = chars[len(legend) % len(chars)]
                        line.append(legend[idx])
                lines.append("".join(line).rstrip())
            blocks.append((view, lines))

        out = []
        for view, lines in blocks:
            header = f"-- {view} " + "-" * max(0, 20 - len(view))
            out.append(header)
            out.extend(lines)
            out.append("")
        if legend and not ansi:
            out.append("legend: " + "  ".join(
                f"{ch}={self.palette.name(i)}" for i, ch in legend.items()))
        out.append(self.stats() + f"   (1 cell = {step} voxel(s))")
        return "\n".join(out)

    # -- render -------------------------------------------------------------

    def render(self, path=None, *, size=512, yaw=30.0, pitch=25.0,
               light=(-0.5, -0.8, 1.0), background="#e8e8e8", margin=0.04,
               anchor=None, scale=None):
        """Orthographic PNG render of the model, as bytes.

        Also writes those bytes to `path` if one is given. `size` is the long
        edge of the model's projected *bounding box*: the canvas crops to that
        box rather than padding out to a square, leaving `margin` of `size` as
        blank border on every edge (and never going below 32 px on an axis).
        Background pixels cost as much to look at as the model does. An
        organic silhouette that stays clear of the box corners lands somewhat
        smaller than `size` -- fitting the box, not the drawn pixels, is what
        keeps a multi-view sweep on one consistent framing.

        `yaw` and `pitch` are in degrees. yaw=0 is the front view, the camera
        looking along +Y exactly as in `preview()`; yaw=90 puts the camera on
        the -X side. `pitch` is elevation above the horizon, so pitch=90 is
        the top view (x right, +Y up), again matching `preview()`.

        `anchor` is a world point -- floats are fine, and a voxel spans
        `[p, p + 1]`, so its center is `p + 0.5` -- whose projection lands on
        the exact center of the canvas. `scale` is pixels per world unit and
        overrides `size` as the magnification (`size` then only sets the
        border). Give two renders the same anchor and scale and the same
        world point sits at the center of both at the same magnification, so
        rounds of a build, or the frames of a turntable, can be laid over each
        other and compared pixel for pixel even though their canvases differ
        in size. Pick the anchor once at the first draft and keep it.

        The whole model always stays on the canvas, so an anchor off to one
        side just leaves more slack on that side.

        `light` is a direction in world space; a low horizontal fill
        opposite its azimuth keeps the faces the key light cannot reach from
        collapsing into one flat tone, while undersides take no fill and stay
        darkest. `background` is anything `parse_color` accepts. Palette
        alpha is ignored -- voxels render opaque.
        """
        if not self.voxels:
            raise ValueError("model is empty")

        # -- camera. d is the direction the camera looks along, so a face is
        # turned toward us when its normal opposes d, and depth grows with d.
        yr, pr = math.radians(yaw), math.radians(pitch)
        cp = math.cos(pr)
        d = _unit((math.sin(yr) * cp, math.cos(yr) * cp, -math.sin(pr)))
        # Straight up or down leaves d x world-up undefined; roll to +Y then.
        up = (0.0, 1.0, 0.0) if abs(d[2]) > 0.9999 else (0.0, 0.0, 1.0)
        right = _unit(_cross(d, up))
        up_s = _cross(right, d)

        # -- fit. A voxel occupies the unit cube [p, p + 1], so the box to
        # frame runs one past the maximum corner on every axis.
        lo, hi = self._extent()
        us, vs = [], []
        for cx in (lo[0], hi[0] + 1):
            for cy in (lo[1], hi[1] + 1):
                for cz in (lo[2], hi[2] + 1):
                    us.append(cx * right[0] + cy * right[1] + cz * right[2])
                    vs.append(cx * up_s[0] + cy * up_s[1] + cz * up_s[2])
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        s = (scale if scale is not None
             else size * (1 - 2 * margin) / (max(u1 - u0, v1 - v0) or 1.0))
        pad = round(size * margin)
        if anchor is None:
            cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
            w = max(_MIN_CANVAS, math.ceil((u1 - u0) * s) + 2 * pad)
            h = max(_MIN_CANVAS, math.ceil((v1 - v0) * s) + 2 * pad)
        else:
            # Grow the canvas symmetrically about the anchor until the far
            # side of the model is in frame: the anchor lands dead center, and
            # the near side just gets more slack than it needs.
            ax, ay, az = anchor
            cu = ax * right[0] + ay * right[1] + az * right[2]
            cv = ax * up_s[0] + ay * up_s[1] + az * up_s[2]
            w = max(_MIN_CANVAS,
                    2 * math.ceil(max(abs(u - cu) for u in us) * s + pad))
            h = max(_MIN_CANVAS,
                    2 * math.ceil(max(abs(v - cv) for v in vs) * s + pad))
        ox = w / 2 - cu * s
        oy = h / 2 + cv * s                      # rows run down, v runs up

        rxs = (right[0] * s, right[1] * s, right[2] * s)
        uxs = (up_s[0] * s, up_s[1] * s, up_s[2] * s)

        # -- per-face constants, computed once for the six normals
        lx, ly, lz = _unit(light)
        # A horizontal fill opposite the key's azimuth. Every face the key
        # reaches gets no fill, so lit faces are untouched; the far walls,
        # which would otherwise all clamp to the same ambient floor and lose
        # the corner between them, get a brightness that still varies with
        # orientation. Horizontal on purpose: undersides take no fill and
        # stay the darkest thing in frame.
        fh = math.hypot(lx, ly)
        fx, fy = (-lx / fh, -ly / fh) if fh > 1e-9 else (0.0, 0.0)
        faces = []
        for normal, corners, tangents in _FACES:
            nx, ny, nz = normal
            if nx * d[0] + ny * d[1] + nz * d[2] >= -1e-9:
                continue                      # turned away from the camera
            offsets = tuple(
                (cx * rxs[0] + cy * rxs[1] + cz * rxs[2],
                 -(cx * uxs[0] + cy * uxs[1] + cz * uxs[2]))
                for cx, cy, cz in corners)
            bright = (_AMBIENT
                      + (1 - _AMBIENT) * max(0.0, nx * lx + ny * ly + nz * lz)
                      + _FILL * max(0.0, nx * fx + ny * fy))
            probes = tuple((nx + tx * sign, ny + ty * sign, nz + tz * sign)
                           for tx, ty, tz in tangents for sign in (1, -1))
            faces.append((normal, offsets, probes, bright))

        rgba = self.palette.rgba
        pats = {}                             # (index, brightness) -> 3 bytes
        bg = parse_color(background)
        fb = bytearray(bytes(bg[:3]) * (w * h))

        vox = self.voxels
        dx, dy, dz = d
        # Back to front: equal-size, non-overlapping cubes under an
        # orthographic camera sort exactly, so nearer voxels simply overwrite.
        for pos in sorted(vox, key=lambda p: p[0] * dx + p[1] * dy + p[2] * dz,
                          reverse=True):
            x, y, z = pos
            idx = vox[pos]
            bx = ox + x * rxs[0] + y * rxs[1] + z * rxs[2]
            by = oy - (x * uxs[0] + y * uxs[1] + z * uxs[2])
            for (nx, ny, nz), offsets, probes, bright in faces:
                if (x + nx, y + ny, z + nz) in vox:
                    continue                  # buried; only the skin is drawn
                for px, py, pz in probes:
                    if (x + px, y + py, z + pz) in vox:
                        bright *= _AO_FACTOR
                pat = pats.get((idx, bright))
                if pat is None:
                    r, g, b, _ = rgba(idx)
                    pat = pats[(idx, bright)] = bytes((
                        min(255, round(r * bright)),
                        min(255, round(g * bright)),
                        min(255, round(b * bright))))
                quad = [(bx + du, by + dv) for du, dv in offsets]
                _fill_quad(fb, w, h, quad, pat)

        data = _write_png(w, h, fb)
        if path is not None:
            with open(path, "wb") as fh:
                fh.write(data)
        return data

    # -- construction from text --------------------------------------------

    @classmethod
    def from_layers(cls, layers, colors, origin=(0, 0, 0)):
        """Build from ASCII art: one multi-line string per Z layer, bottom up.

        Within a layer the first text row is the highest Y, so each layer
        reads like a top-down map. '.' and ' ' are always empty; every other
        character is looked up in `colors` (map a char to None to skip it).

            Model.from_layers(["##\\n##", ".#\\n#."], {"#": "red"})
        """
        m = cls()
        for z, layer in enumerate(layers):
            # Trim only the blank lines that triple-quoting adds at the ends;
            # interior blank rows are real empty rows of Y.
            rows = layer.splitlines()
            while rows and not rows[0].strip():
                rows.pop(0)
            while rows and not rows[-1].strip():
                rows.pop()
            height = len(rows)
            for row_i, row in enumerate(rows):
                y = height - 1 - row_i
                for x, ch in enumerate(row):
                    if ch in ". ":
                        continue
                    if ch not in colors:
                        raise ValueError(
                            f"layer {z} uses {ch!r}, which is not in the color "
                            f"map; map it to None if it should be empty"
                        )
                    if colors[ch] is None:
                        continue
                    m.voxel((origin[0] + x, origin[1] + y, origin[2] + z),
                            colors[ch])
        return m


# --------------------------------------------------------------------------
# scene -- a world of chunk-sized models, past the 256^3 limit
# --------------------------------------------------------------------------

class Scene:
    """A world larger than one model, written as a multi-model .vox file.

    A `Model` is capped at 256 voxels per axis because that is all a single
    SIZE/XYZI pair can address. A `Scene` bins voxels into 256^3 chunks, one
    model each, and writes the scene-graph chunks (nTRN/nGRP/nSHP) that
    position them -- so a 1024^3 world is 64 chunks in one file.

        s = Scene()
        for i in range(4):
            tower = build_tower(i)
            s.place(tower, offset=(i * 300, 0, 0))
            del tower                      # the scene kept a copy, not a ref
        print(s.save("world.vox"))

    Coordinates are world coordinates throughout and may be negative; `save`
    shifts the lowest occupied chunk to chunk (0, 0, 0). Read the result back
    with `Model.load`, which honors the transforms -- though a world that
    needed a Scene to write may well be too big to want as one flat Model.
    """

    CHUNK = 256

    def __init__(self, palette=None):
        # (ci, cj, ck) -> {(lx, ly, lz): palette index}. Nested dicts rather
        # than one flat dict of world coordinates: the local keys are what
        # get written, so nothing has to be re-derived at save time.
        self.chunks = {}
        self.palette = palette or Palette()

    def _put(self, x, y, z, idx):
        """File one world-coordinate voxel into its chunk.

        Python's // and % floor toward negative infinity, which is exactly
        the binning we want: -1 lands at local 255 of chunk -1.
        """
        n = self.CHUNK
        key = (x // n, y // n, z // n)
        cell = self.chunks.get(key)
        if cell is None:
            cell = self.chunks[key] = {}
        cell[(x % n, y % n, z % n)] = idx

    # -- placement ----------------------------------------------------------

    def place(self, model, offset=(0, 0, 0)):
        """Bin `model`'s voxels, shifted by `offset`, into the scene.

        Colors are re-interned by RGBA value, so `model` may carry any
        palette -- its indices are not assumed to mean anything here.

        No reference to `model` survives the call, which is the point of the
        design: build a world one piece at a time and drop each piece as
        soon as it is placed.
        """
        ox, oy, oz = offset
        remap = {}
        for (x, y, z), idx in model.voxels.items():
            mine = remap.get(idx)
            if mine is None:
                mine = remap[idx] = self.palette.index(model.palette.rgba(idx))
            self._put(x + ox, y + oy, z + oz, mine)
        return self

    def add(self, coords, color):
        """Paint `coords`, in world coordinates, with `color`."""
        idx = self.palette.index(color)
        for c in coords:
            self._put(c[0], c[1], c[2], idx)
        return self

    def voxel(self, pos, color):
        return self.add({tuple(pos)}, color)

    # -- queries ------------------------------------------------------------

    def __len__(self):
        return sum(len(cell) for cell in self.chunks.values())

    @property
    def bounds(self):
        """(min_corner, max_corner) in world coordinates, or None if empty."""
        lo = hi = None
        n = self.CHUNK
        for key, cell in self.chunks.items():
            if not cell:
                continue
            xs, ys, zs = zip(*cell)
            clo = (key[0] * n + min(xs), key[1] * n + min(ys),
                   key[2] * n + min(zs))
            chi = (key[0] * n + max(xs), key[1] * n + max(ys),
                   key[2] * n + max(zs))
            lo = clo if lo is None else tuple(map(min, lo, clo))
            hi = chi if hi is None else tuple(map(max, hi, chi))
        return None if lo is None else (lo, hi)

    @property
    def size(self):
        """Extent in voxels along each axis."""
        b = self.bounds
        if b is None:
            return (0, 0, 0)
        lo, hi = b
        return tuple(hi[i] - lo[i] + 1 for i in range(3))

    def chunk_stats(self):
        """Voxel count per occupied chunk, keyed by chunk index."""
        return {key: len(cell) for key, cell in self.chunks.items() if cell}

    # -- io -----------------------------------------------------------------

    def save(self, path):
        """Write a multi-model .vox file. Returns a summary string.

        The scene is shifted by *whole chunks* so the lowest occupied chunk
        becomes chunk (0, 0, 0). A whole-chunk shift leaves every local
        coordinate alone, so nothing is rebinned -- only the translations
        written into the scene graph change.
        """
        filled = sorted(key for key, cell in self.chunks.items() if cell)
        if not filled:
            raise ValueError("refusing to save an empty scene")
        shift = tuple(-min(key[i] for key in filled) for i in range(3))

        n = self.CHUNK
        half = n // 2
        models, nodes, children = [], [], []
        for k, key in enumerate(filled):
            cell = self.chunks[key]
            # Every chunk is written at the full CHUNK^3 size rather than
            # shrunk to its own bounding box. That is deliberate: with all
            # sizes equal, the center-vs-corner convention below is either
            # right for every chunk or wrong for every chunk, so a mistake
            # slides the whole world uniformly instead of tearing chunks
            # apart at their seams. SIZE is 12 fixed bytes and XYZI stores
            # only filled voxels, so it costs nothing.
            models.append(_chunk(b"SIZE", struct.pack("<iii", n, n, n)))
            models.append(_chunk(b"XYZI", struct.pack("<i", len(cell))
                                 + b"".join(bytes((x, y, z, i))
                                            for (x, y, z), i in cell.items())))
            # `_t` is the position of the model's *center*, not its minimum
            # corner: a loader puts the corner at _t - size // 2. So a chunk
            # whose world minimum corner is (cx, cy, cz) is translated to
            # (cx + 128, cy + 128, cz + 128).
            node = 2 + 2 * k
            children.append(node)
            trans = " ".join(str((key[i] + shift[i]) * n + half)
                             for i in range(3))
            nodes.append(_chunk(b"nTRN",
                                struct.pack("<i", node) + _dict_bytes({})
                                + struct.pack("<iiii", node + 1, -1, 0, 1)
                                + _dict_bytes({"_t": trans})))
            nodes.append(_chunk(b"nSHP",
                                struct.pack("<i", node + 1) + _dict_bytes({})
                                + struct.pack("<ii", 1, k) + _dict_bytes({})))

        # MagicaVoxel wants the root to be an nTRN (node 0, layer -1) over a
        # single nGRP holding one transform per chunk.
        root = _chunk(b"nTRN", struct.pack("<i", 0) + _dict_bytes({})
                      + struct.pack("<iiii", 1, -1, -1, 1)
                      + _dict_bytes({"_t": "0 0 0"}))
        group = _chunk(b"nGRP", struct.pack("<i", 1) + _dict_bytes({})
                       + struct.pack("<i", len(children))
                       + b"".join(struct.pack("<i", c) for c in children))

        body = b"".join(models + [root, group] + nodes
                        + [_chunk(b"RGBA", self.palette.chunk_bytes())])
        data = b"VOX " + struct.pack("<i", 150) + _chunk(b"MAIN", b"", body)
        with open(path, "wb") as f:
            f.write(data)
        return (f"wrote {path}: {len(self)} voxels, size {self.size}, "
                f"{len(filled)} chunks, {len(self.palette)} colors, "
                f"{len(data)} bytes")


# --------------------------------------------------------------------------
# .vox chunk plumbing
# --------------------------------------------------------------------------

def _chunk(cid, content=b"", children=b""):
    return cid + struct.pack("<ii", len(content), len(children)) + content + children


def _walk_chunks(data, offset, end=None):
    """Yield (id, content) for every chunk under `offset`, recursing into children."""
    end = len(data) if end is None else end
    while offset + 12 <= end:
        cid = data[offset:offset + 4]
        n, m = struct.unpack_from("<ii", data, offset + 4)
        content = data[offset + 12:offset + 12 + n]
        yield cid, content
        kids = offset + 12 + n
        yield from _walk_chunks(data, kids, kids + m)
        offset = kids + m


# The scene-graph chunks (nTRN/nGRP/nSHP) carry their attributes as DICTs of
# STRINGs: an int32 count, then that many key/value pairs, each a int32
# byte-length followed by UTF-8 bytes.

def _string_bytes(s):
    raw = s.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def _dict_bytes(d):
    return struct.pack("<i", len(d)) + b"".join(
        _string_bytes(k) + _string_bytes(v) for k, v in d.items())


def _read_string(data, offset):
    """(text, offset just past it)."""
    n = struct.unpack_from("<i", data, offset)[0]
    raw = data[offset + 4:offset + 4 + n]
    return raw.decode("utf-8", "replace"), offset + 4 + n


def _read_dict(data, offset):
    """(dict of str to str, offset just past it)."""
    n = struct.unpack_from("<i", data, offset)[0]
    offset += 4
    out = {}
    for _ in range(n):
        key, offset = _read_string(data, offset)
        val, offset = _read_string(data, offset)
        out[key] = val
    return out, offset


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def _render_cli(argv):
    """The `render` subcommand: one PNG, or one per repeated --view."""
    src, out = argv[2], None
    size, yaw, pitch, views = 512, 30.0, 25.0, []
    anchor, scale = None, None
    takes_value = ("--size", "--yaw", "--pitch", "--view", "--anchor",
                   "--scale")
    rest, i = argv[3:], 0
    while i < len(rest):
        arg = rest[i]
        if arg in takes_value:
            if i + 1 >= len(rest):
                print(f"{arg} needs a value")
                return 2
            val = rest[i + 1]
            if arg == "--size":
                size = int(val)
            elif arg == "--yaw":
                yaw = float(val)
            elif arg == "--pitch":
                pitch = float(val)
            elif arg == "--scale":
                scale = float(val)
            elif arg == "--anchor":
                parts = val.split(",")
                if len(parts) != 3:
                    print(f"--anchor wants X,Y,Z, not {val!r}")
                    return 2
                anchor = tuple(float(p) for p in parts)
            else:
                y, comma, p = val.partition(",")
                if not comma:
                    print(f"--view wants YAW,PITCH, not {val!r}")
                    return 2
                views.append((float(y), float(p)))
            i += 2
        elif arg.startswith("--"):
            print(f"unknown option {arg}")
            return 2
        else:
            out = arg
            i += 1

    m = Model.load(src)
    if views:
        stem = os.path.splitext(out or src)[0]
        paths = [f"{stem}_y{vy:g}p{vp:g}.png" for vy, vp in views]
    else:
        views = [(yaw, pitch)]
        paths = [out or os.path.splitext(src)[0] + ".png"]
    for (vy, vp), path in zip(views, paths):
        m.render(path, size=size, yaw=vy, pitch=vp, anchor=anchor, scale=scale)
        print(path)
    return 0


def _main(argv):
    if len(argv) >= 3 and argv[1] == "render":
        return _render_cli(argv)
    if len(argv) >= 3 and argv[1] in ("preview", "info", "check"):
        m = Model.load(argv[2])
        if argv[1] == "info":
            print(m.stats())
            for name, n in m.color_histogram():
                print(f"  {n:>8}  {name}")
        elif argv[1] == "check":
            loose = m.detached()
            if not loose:
                print(f"ok: all {len(m)} voxels are one connected piece")
                return 0
            groups = m.components()[1:]      # everything but the main body
            print(f"{len(loose)} detached voxel(s) in {len(groups)} piece(s):")
            for c in groups[:10]:
                lo = tuple(min(p[i] for p in c) for i in range(3))
                hi = tuple(max(p[i] for p in c) for i in range(3))
                print(f"  {len(c):>6} voxels at {lo}..{hi}")
            return 1
        else:
            print(m.preview(ansi="--ansi" in argv))
        return 0
    print(__doc__)
    print("usage: voxel.py preview <file.vox> [--ansi]\n"
          "       voxel.py info <file.vox>\n"
          "       voxel.py check <file.vox>   # find floating parts\n"
          "       voxel.py render <file.vox> [out.png] [--size N]\n"
          "                  [--yaw D] [--pitch D] [--view YAW,PITCH ...]\n"
          "                  [--anchor X,Y,Z] [--scale S]")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
