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

import math
import struct
import sys
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
    (r, g, b) / (r, g, b, a) sequence.
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
    vals = tuple(int(v) for v in c)
    if len(vals) == 3:
        vals += (255,)
    if len(vals) != 4 or not all(0 <= v <= 255 for v in vals):
        raise ValueError(f"unrecognized color {c!r}")
    return vals


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
        seen = {seed}
        stack = [seed]
        while stack:
            x, y, z = stack.pop()
            for n in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z),
                      (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)):
                if n in self.voxels and n not in seen:
                    seen.add(n)
                    stack.append(n)
        return seen

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
        remaining = set(self.voxels)
        out = []
        while remaining:
            component = self._flood(next(iter(remaining)))
            out.append(component)
            remaining -= component
        return sorted(out, key=len, reverse=True)

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

def _main(argv):
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
          "       voxel.py check <file.vox>   # find floating parts")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
