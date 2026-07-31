"""Tests for voxel.py. Run directly (python3 test_voxel.py) or under pytest."""

import math
import os
import struct
import sys
import tempfile
import zlib

from voxel import (MAX_DIM, Model, Palette, Scene, bounds, chroma, components,
                   disc_average, fbm3, luma, match_histogram, mirror, noise3,
                   parse_color, ramp_at, relight, rotate90, scale, scale_color,
                   shapes, to_hex, translate, weighted_quantiles, _main,
                   _walk_chunks)


def _tmp(name="t.vox"):
    return os.path.join(tempfile.mkdtemp(prefix="voxtest"), name)


# -- format correctness ----------------------------------------------------

def test_header_and_chunk_layout():
    m = Model()
    m.voxel((0, 0, 0), "red")
    path = _tmp()
    m.save(path)
    data = open(path, "rb").read()
    assert data[:4] == b"VOX "
    assert struct.unpack_from("<i", data, 4)[0] == 150
    ids = [cid for cid, _ in _walk_chunks(data, 8)]
    assert ids == [b"MAIN", b"SIZE", b"XYZI", b"RGBA"]


def test_palette_off_by_one():
    """XYZI index n must resolve to RGBA table entry n-1."""
    m = Model()
    m.voxel((0, 0, 0), (10, 20, 30))
    path = _tmp()
    m.save(path)
    data = open(path, "rb").read()
    chunks = dict(_walk_chunks(data, 8))

    count = struct.unpack_from("<i", chunks[b"XYZI"], 0)[0]
    assert count == 1
    x, y, z, idx = chunks[b"XYZI"][4:8]
    assert (x, y, z) == (0, 0, 0)
    assert idx == 1, "first color must be palette index 1, not 0"

    table = chunks[b"RGBA"]
    assert len(table) == 1024, "RGBA chunk is always 256 entries"
    assert tuple(table[0:4]) == (10, 20, 30, 255), "index 1 lives at entry 0"


def test_round_trip_preserves_voxels_and_colors():
    m = Model()
    m.sphere((0, 0, 0), 6, "green")
    m.box((-8, -8, -10), (8, 8, -8), "stone")
    m.voxel((0, 0, 9), (200, 10, 90))
    path = _tmp()
    m.save(path)
    back = Model.load(path)

    assert len(back) == len(m)
    lo, _ = m.bounds
    shifted = {(x - lo[0], y - lo[1], z - lo[2]): i
               for (x, y, z), i in m.voxels.items()}
    assert set(back.voxels) == set(shifted)
    for pos, idx in shifted.items():
        assert back.palette.rgba(back.voxels[pos]) == m.palette.rgba(idx)


def test_size_limit_is_reported_clearly():
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.voxel((MAX_DIM, 0, 0), "red")
    try:
        m.save(_tmp())
    except ValueError as e:
        assert "257" in str(e) and "x" in str(e)
    else:
        raise AssertionError("oversized model should not save")


def test_empty_model_refuses_to_save():
    try:
        Model().save(_tmp())
    except ValueError:
        pass
    else:
        raise AssertionError("empty model should not save")


def test_negative_coordinates_normalize_to_origin():
    m = Model()
    m.box((-5, -5, -5), (-3, -3, -3), "blue")
    path = _tmp()
    m.save(path)
    back = Model.load(path)
    assert bounds(back.coords()) == ((0, 0, 0), (2, 2, 2))


# -- palette ---------------------------------------------------------------

def test_color_parsing_forms():
    assert parse_color("#ff8800") == (255, 136, 0, 255)
    assert parse_color("#f80") == (255, 136, 0, 255)
    assert parse_color("#ff880080") == (255, 136, 0, 128)
    assert parse_color((1, 2, 3)) == (1, 2, 3, 255)
    assert parse_color("red") == parse_color("#d13b3b")


def test_palette_dedupes_and_passes_through_indices():
    p = Palette()
    assert p.index("red") == 1
    assert p.index("red") == 1, "identical colors must share an index"
    assert p.index("blue") == 2
    assert p.index(7) == 7, "explicit indices pass through"


def test_palette_capacity():
    p = Palette()
    for i in range(255):
        p.index((i, 0, 0))
    try:
        p.index((0, 1, 0))
    except ValueError as e:
        assert "255" in str(e)
    else:
        raise AssertionError("256th color should be rejected")


# -- shapes ----------------------------------------------------------------

def test_box_is_inclusive_and_order_independent():
    a = shapes.box((0, 0, 0), (2, 2, 2))
    assert len(a) == 27
    assert a == shapes.box((2, 2, 2), (0, 0, 0))


def test_hollow_box_wall_thickness_is_exact():
    solid = shapes.box((0, 0, 0), (9, 9, 9))
    inner = shapes.box((2, 2, 2), (7, 7, 7))
    assert shapes.box((0, 0, 0), (9, 9, 9), hollow=True, thickness=2) == solid - inner


def test_sphere_is_symmetric_and_correctly_sized():
    s = shapes.sphere((0, 0, 0), 5)
    assert bounds(s) == ((-5, -5, -5), (5, 5, 5))
    for axis in "xyz":
        assert mirror(s, axis) == s, f"sphere not symmetric about {axis}"


def test_cylinder_extends_along_its_axis():
    c = shapes.cylinder((0, 0, 0), 3, 10, axis="y")
    lo, hi = bounds(c)
    assert (lo[1], hi[1]) == (0, 9)
    assert (lo[0], hi[0]) == (-3, 3)


def test_cone_tapers_to_a_point():
    c = shapes.cone((0, 0, 0), 6, 12)
    base = {p for p in c if p[2] == 0}
    tip = {p for p in c if p[2] == 11}
    assert len(base) > len(tip)
    assert len(tip) == 1


def test_line_connects_endpoints():
    ln = shapes.line((0, 0, 0), (10, 5, -3))
    assert (0, 0, 0) in ln and (10, 5, -3) in ln
    assert len(ln) == 11, "one voxel per step along the dominant axis"


def test_set_algebra_makes_shells():
    shell = shapes.sphere((0, 0, 0), 6) - shapes.sphere((0, 0, 0), 4)
    assert shell and (0, 0, 0) not in shell
    assert (0, 0, 6) in shell


def test_where_predicate():
    diag = shapes.where((0, 0, 0), (4, 4, 0), lambda x, y, z: x == y)
    assert diag == {(i, i, 0) for i in range(5)}


def test_frustum_interpolates_between_its_radii():
    f = shapes.frustum((0, 0, 0), 6, 10, top_radius=2)
    base = {p for p in f if p[2] == 0}
    top = {p for p in f if p[2] == 9}
    assert bounds(base)[1][0] == 6 and bounds(top)[1][0] == 2
    assert len(base) > len(top) > 1


def test_cone_is_a_frustum_with_a_zero_radius_end():
    assert shapes.cone((0, 0, 0), 5, 9) == shapes.frustum((0, 0, 0), 5, 9, 0)
    assert (shapes.cone((0, 0, 0), 5, 9, invert=True)
            == shapes.frustum((0, 0, 0), 0, 9, 5))


def test_wedge_tapers_along_one_axis_and_keeps_a_layer():
    w = shapes.wedge((0, 0, 0), (9, 2, 5), axis="x", taper="z")
    assert max(z for x, y, z in w if x == 0) == 5, "full at the low end"
    assert max(z for x, y, z in w if x == 9) == 0, "one layer at the high end"
    assert {y for _, y, _ in w} == {0, 1, 2}, "third axis is untouched"
    assert all(0 <= z <= 5 for _, _, z in w)


def test_wedge_invert_swaps_the_thick_end():
    a = shapes.wedge((0, 0, 0), (7, 0, 4), axis="x", taper="z")
    b = shapes.wedge((0, 0, 0), (7, 0, 4), axis="x", taper="z", invert=True)
    assert a == {(7 - x, y, z) for x, y, z in b}, "a flip about the x midpoint"


def test_polygon_fills_and_extrudes():
    square = shapes.polygon([(0, 0), (4, 0), (4, 4), (0, 4)], 0, 3)
    assert square == shapes.box((0, 0, 0), (4, 4, 2))


def test_polygon_keeps_a_thin_outline():
    """A spike thinner than a cell must not vanish from the even-odd fill."""
    tri = shapes.polygon([(0, 0), (20, 1), (0, 2)], 0, 1)
    assert (20, 1, 0) in tri, "the tip survives"
    assert (0, 1, 0) in tri


def test_polygon_axis_picks_the_other_two_coords():
    p = shapes.polygon([(0, 0), (3, 0), (3, 3), (0, 3)], 5, 2, axis="y")
    lo, hi = bounds(p)
    assert (lo[1], hi[1]) == (5, 6), "extruded along y"
    assert (lo[0], hi[0]) == (0, 3) and (lo[2], hi[2]) == (0, 3)


def test_helix_winds_around_its_axis():
    h = shapes.helix((0, 0, 0), 5, 20, turns=2)
    lo, hi = bounds(h)
    assert (lo[2], hi[2]) == (0, 19)
    assert (lo[0], hi[0]) == (-5, 5) and (lo[1], hi[1]) == (-5, 5)
    assert (0, 0, 10) not in h, "hollow in the middle"


# -- silhouette hull -------------------------------------------------------

def test_silhouette_hull_of_three_squares_is_a_box():
    n = 5
    square = ["#" * n] * n
    hull = shapes.silhouette_hull(front=square, side=square, top=square)
    assert hull == shapes.box((0, 0, 0), (n - 1, n - 1, n - 1))


def test_silhouette_hull_extrudes_a_drawn_circle():
    """A circle on top plus full front and side masks is a cylinder."""
    top = ["..###..",
           ".#####.",
           "#######",
           "#######",
           "#######",
           ".#####.",
           "..###.."]
    height = 5
    rect = ["#" * 7] * height
    hull = shapes.silhouette_hull(front=rect, side=rect, top=top)

    circle = {(x, len(top) - 1 - i)
              for i, row in enumerate(top)
              for x, ch in enumerate(row) if ch != "."}
    assert hull == {(x, y, z) for x, y in circle for z in range(height)}


def test_silhouette_hull_front_notch_lands_top_left():
    """Regression: row 0 is the highest z and column 0 is the lowest x."""
    n = 4
    full = ["#" * n] * n
    notched = ["." + "#" * (n - 1)] + ["#" * n] * (n - 1)
    hull = shapes.silhouette_hull(front=notched, side=full, top=full)
    box = shapes.box((0, 0, 0), (n - 1, n - 1, n - 1))
    assert box - hull == {(0, y, n - 1) for y in range(n)}


def test_silhouette_hull_top_notch_lands_at_max_y():
    """Regression: the top mask's first text row is the highest y."""
    n = 4
    full = ["#" * n] * n
    notched = ["." + "#" * (n - 1)] + ["#" * n] * (n - 1)
    hull = shapes.silhouette_hull(front=full, side=full, top=notched)
    box = shapes.box((0, 0, 0), (n - 1, n - 1, n - 1))
    assert box - hull == {(0, n - 1, z) for z in range(n)}


def test_silhouette_hull_from_two_masks():
    front = [".#.",
             "###"]
    side = ["##",
            "##"]
    hull = shapes.silhouette_hull(front=front, side=side)
    face = {(1, 1), (0, 0), (1, 0), (2, 0)}          # (x, z)
    assert hull == {(x, y, z) for x, z in face for y in range(2)}
    lo, hi = bounds(hull)
    assert (lo, hi) == ((0, 0, 0), (2, 1, 1)), "y extent comes from side"


def test_silhouette_hull_rejects_disagreeing_extents():
    try:
        shapes.silhouette_hull(front=["####"] * 3, top=["###"] * 3)
    except ValueError as e:
        assert "front" in str(e) and "top" in str(e)
        assert "4" in str(e) and "3" in str(e)
    else:
        assert False, "mismatched x extents must raise"


def test_silhouette_hull_needs_two_masks():
    try:
        shapes.silhouette_hull(front=["##", "##"])
    except ValueError as e:
        assert "two" in str(e)
    else:
        assert False, "one mask leaves an axis unbounded"


def test_silhouette_hull_accepts_a_multiline_string():
    rows = [".##.", "####", "####"]
    assert (shapes.silhouette_hull(front="\n".join(rows), side="\n".join(rows))
            == shapes.silhouette_hull(front=rows, side=rows))


def test_silhouette_hull_front_mask_matches_its_preview():
    """The drawing and preview() must agree cell for cell."""
    front = [".##.",         # asymmetric both ways, so a flipped mask shows
             "####",
             "####",
             "#..."]
    m = Model()
    m.add(shapes.silhouette_hull(front=front, side=["#" * 4] * 4), "red")

    lines = m.preview(max_dim=64, views=("front",)).splitlines()
    block = lines[1:lines.index("", 1)]
    shown = {(c, len(block) - 1 - i)
             for i, row in enumerate(block)
             for c, ch in enumerate(row) if ch != " "}
    drawn = {(c, len(front) - 1 - i)
             for i, row in enumerate(front)
             for c, ch in enumerate(row) if ch != "."}
    assert shown == drawn


# -- transforms ------------------------------------------------------------

def test_translate_and_mirror():
    s = {(1, 2, 3)}
    assert translate(s, (10, 0, -3)) == {(11, 2, 0)}
    assert mirror(s, "x", at=0) == {(-1, 2, 3)}
    assert mirror(s, "x", at=5) == {(9, 2, 3)}


def test_rotate90_four_turns_is_identity():
    s = shapes.box((0, 0, 0), (3, 1, 2))
    assert rotate90(s, "z", 4) == s
    assert rotate90(s, "z", 1) != s


def test_scale_expands_each_voxel_to_a_cube():
    assert len(scale({(0, 0, 0), (5, 5, 5)}, 3)) == 2 * 27


# -- model semantics -------------------------------------------------------

def test_add_overwrites_and_add_under_does_not():
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.add({(0, 0, 0)}, "blue")
    assert m.palette.rgba(m.voxels[(0, 0, 0)]) == parse_color("blue")
    m.add_under({(0, 0, 0)}, "green")
    assert m.palette.rgba(m.voxels[(0, 0, 0)]) == parse_color("blue")


def test_remove_and_keep():
    m = Model()
    m.box((0, 0, 0), (4, 4, 4), "red")
    m.remove(shapes.box((0, 0, 0), (1, 1, 1)))
    assert (0, 0, 0) not in m and (4, 4, 4) in m
    m.keep(shapes.box((3, 3, 3), (4, 4, 4)))
    assert len(m) == 8


def test_model_mirror_keeps_both_halves():
    m = Model()
    m.box((1, 0, 0), (3, 0, 0), "red")
    m.mirror("x", at=0)
    assert len(m) == 6
    assert (-3, 0, 0) in m and (3, 0, 0) in m


def test_merge_remaps_palette_indices():
    a = Model()
    a.voxel((0, 0, 0), "red")
    b = Model()
    b.voxel((0, 0, 0), "blue")     # index 1 in b, must not collide with a's red
    a.merge(b, offset=(5, 0, 0))
    assert a.palette.rgba(a.voxels[(0, 0, 0)]) == parse_color("red")
    assert a.palette.rgba(a.voxels[(5, 0, 0)]) == parse_color("blue")


def test_copy_is_independent():
    m = Model()
    m.voxel((0, 0, 0), "red")
    c = m.copy()
    c.voxel((1, 1, 1), "blue")
    assert len(m) == 1 and len(c) == 2


def test_size_and_bounds():
    m = Model()
    m.box((-2, 0, 0), (2, 0, 3), "red")
    assert m.size == (5, 1, 4)
    assert m.bounds == ((-2, 0, 0), (2, 0, 3))


def test_model_rotate90_carries_colors():
    m = Model()
    m.voxel((3, 0, 0), "red")
    m.voxel((0, 3, 0), "blue")
    red, blue = m.palette.index("red"), m.palette.index("blue")
    m.rotate90("z", 1)
    assert m.voxels == {(0, -3, 0): red, (3, 0, 0): blue}


def test_model_rotate90_four_turns_is_identity():
    m = Model()
    m.box((0, 0, 0), (4, 2, 1), "green")
    before = dict(m.voxels)
    assert m.rotate90("y", 4).voxels == before


def test_model_scale_keeps_colors():
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.scale(3)
    assert len(m) == 27 and len(set(m.voxels.values())) == 1
    assert m.size == (3, 3, 3)


def test_recolor_repaints_one_color_only():
    m = Model()
    m.box((0, 0, 0), (3, 3, 3), "red")
    m.box((0, 0, 0), (3, 3, 0), "blue")
    n = m.recolor("red", "green")
    assert n == 48                                # 64 minus the 16 blue
    assert m.palette.index("green") in m.voxels.values()
    assert m.palette.index("red") not in m.voxels.values()


def test_center_moves_the_bounding_box_onto_the_origin():
    m = Model()
    m.box((10, 20, 5), (16, 30, 9), "stone")
    m.center("xy")
    lo, hi = m.bounds
    assert lo[0] == -hi[0] and lo[1] == -hi[1]
    assert (lo[2], hi[2]) == (5, 9), "z untouched by default"


def test_surface_excludes_the_interior():
    m = Model()
    m.box((0, 0, 0), (4, 4, 4), "stone")
    assert (2, 2, 2) not in m.surface()
    assert len(m.surface()) == 125 - 27


def test_surface_facing_selects_one_side():
    m = Model()
    m.box((0, 0, 0), (3, 3, 3), "stone")
    top = m.surface("z+")
    assert top == {(x, y, 3) for x in range(4) for y in range(4)}


def test_support_counts_what_actually_rests_on_something():
    """The check detached() cannot make: connected but perched on air."""
    m = Model()
    m.box((0, 0, 0), (9, 9, 0), "stone")      # floor
    m.box((2, 2, 1), (5, 5, 3), "metal")      # a crate sitting on it
    m.box((2, 2, 5), (5, 5, 7), "metal")      # ...and one floating above
    m.voxel((2, 2, 4), "metal")               # joined by a single strut

    assert m.detached() == set(), "connectivity says the whole thing is fine"
    seated = {(x, y, 1) for x in range(2, 6) for y in range(2, 6)}
    floating = {(x, y, 5) for x in range(2, 6) for y in range(2, 6)}
    assert m.support(seated) == (16, 16)
    assert m.support(floating) == (1, 16)


# -- from_layers -----------------------------------------------------------

def test_from_layers_orientation():
    """First text row is the highest Y; layer 0 is the lowest Z."""
    m = Model.from_layers(
        ["ab\n.c",      # z=0: row 0 is y=1, row 1 is y=0
         "d."],         # z=1
        {"a": "red", "b": "blue", "c": "green", "d": "yellow"})
    assert len(m) == 4
    assert (0, 1, 0) in m, "'a' is top-left of layer 0 -> x=0, highest y"
    assert (1, 1, 0) in m, "'b' is top-right -> x=1, highest y"
    assert (1, 0, 0) in m, "'c' is bottom-right -> x=1, y=0"
    assert (0, 0, 1) in m, "'d' is the only row of layer 1 -> y=0, z=1"


def test_from_layers_skips_dots_and_none():
    m = Model.from_layers(["#.#"], {"#": "red", ".": None})
    assert len(m) == 2


# -- connectivity ----------------------------------------------------------

def test_detached_is_empty_for_a_solid_model():
    m = Model()
    m.box((0, 0, 0), (4, 4, 4), "red")
    assert m.detached() == set()


def test_detached_finds_a_floating_part():
    """The cake bug: a decoration seated one voxel above its surface."""
    m = Model()
    m.box((0, 0, 0), (9, 9, 1), "stone")     # a base plate
    m.box((4, 4, 3), (5, 5, 6), "red")       # candle, floating at z=3
    loose = m.detached()
    assert loose == shapes.box((4, 4, 3), (5, 5, 6))

    m.box((4, 4, 2), (5, 5, 2), "red")       # close the gap
    assert m.detached() == set()


def test_detached_seeds_from_the_lowest_voxel():
    """Default seed is the base, so the big floating part is what's reported."""
    m = Model()
    m.voxel((0, 0, 0), "stone")              # lone base voxel
    m.box((0, 0, 5), (3, 3, 8), "red")       # larger, but floating
    assert len(m.detached()) == 64


def test_detached_treats_corner_contact_as_separate():
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.voxel((1, 1, 1), "red")                # touches only at a corner
    assert m.detached() == {(1, 1, 1)}


def test_detached_accepts_an_explicit_seed():
    m = Model()
    m.box((0, 0, 0), (1, 1, 1), "red")
    m.box((5, 5, 5), (6, 6, 6), "blue")
    assert m.detached(seed=(5, 5, 5)) == shapes.box((0, 0, 0), (1, 1, 1))


def test_detached_rejects_an_empty_seed():
    m = Model()
    m.voxel((0, 0, 0), "red")
    try:
        m.detached(seed=(9, 9, 9))
    except ValueError as e:
        assert "not a filled voxel" in str(e)
    else:
        raise AssertionError("seed on empty space should raise")


def test_detached_of_empty_model():
    assert Model().detached() == set()


def test_components_are_sorted_largest_first():
    m = Model()
    m.box((0, 0, 0), (3, 3, 3), "red")       # 64
    m.box((9, 9, 9), (10, 10, 10), "blue")   # 8
    m.voxel((20, 20, 20), "green")           # 1
    assert [len(c) for c in m.components()] == [64, 8, 1]
    assert Model().components() == []


# -- preview ---------------------------------------------------------------

def test_preview_runs_and_reports_scale():
    m = Model()
    m.sphere((0, 0, 0), 4, "red")
    out = m.preview()
    assert "front" in out and "side" in out and "top" in out
    assert "legend" in out
    assert m.preview(ansi=True).count("\x1b") > 0


def test_preview_downsamples_large_models():
    m = Model()
    m.box((0, 0, 0), (99, 99, 99), "red")
    out = m.preview(max_dim=20).splitlines()
    assert "1 cell = 5 voxel(s)" in out[-1]
    grid = out[out.index("-- front ---------------") + 1:]
    grid = grid[:grid.index("")]                  # rows of the front view only
    assert len(grid) == 20 and max(len(r) for r in grid) == 20


def test_preview_of_empty_model():
    assert Model().preview() == "empty model"


# -- render (png) ----------------------------------------------------------

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BG = bytes(parse_color("#e8e8e8")[:3])


def _decode_png(data):
    """(width, height, rows) from a filter-0 truecolor PNG. Rows are bytes."""
    assert data[:8] == PNG_MAGIC
    width = height = None
    idat = bytearray()
    off = 8
    while off < len(data):
        n = struct.unpack_from(">I", data, off)[0]
        tag = data[off + 4:off + 8]
        body = data[off + 8:off + 8 + n]
        if tag == b"IHDR":
            width, height, depth, ctype = struct.unpack_from(">IIBB", body, 0)
            assert (depth, ctype) == (8, 2), "want 8-bit truecolor"
        elif tag == b"IDAT":
            idat += body
        off += 12 + n                             # len + tag + body + crc
        assert struct.unpack_from(">I", data, off - 4)[0] == \
            zlib.crc32(tag + body) & 0xFFFFFFFF
    raw = zlib.decompress(bytes(idat))
    stride = width * 3
    assert len(raw) == height * (stride + 1)
    rows = []
    for r in range(height):
        p = r * (stride + 1)
        assert raw[p] == 0, "every row must use filter 0"
        rows.append(raw[p + 1:p + 1 + stride])
    return width, height, rows


def _pixels(rows, width):
    """[(row, col, rgb), ...] for every pixel that is not the background."""
    return [(r, c, row[c * 3:c * 3 + 3])
            for r, row in enumerate(rows)
            for c in range(width)
            if row[c * 3:c * 3 + 3] != BG]


def _of_hue(rows, width, channel):
    """Pixels whose brightest channel is `channel` (0=r, 1=g, 2=b).

    Shading multiplies all three channels by one factor, so which channel
    leads survives it and identifies the color a pixel was painted with.
    """
    return [(r, c) for r, c, p in _pixels(rows, width)
            if p[channel] == max(p) and p.count(max(p)) == 1]


def test_render_writes_the_bytes_it_returns():
    m = Model()
    m.box((0, 0, 0), (3, 3, 3), "red")
    path = _tmp("r.png")
    data = m.render(path, size=40)
    assert isinstance(data, (bytes, bytearray))
    assert open(path, "rb").read() == data
    w, h, rows = _decode_png(data)
    assert len(rows) == h and len(rows[0]) == w * 3


def test_render_crops_the_short_axis_to_the_content():
    """size is the long edge of the content, not of a square canvas."""
    m = Model()
    m.box((0, 0, 0), (3, 0, 7), "red")          # 4 wide, 8 tall, seen head-on
    w, h, _ = _decode_png(m.render(size=40, yaw=0, pitch=0))
    # content 40 * (1 - 2*0.04) = 36.8 px tall, plus round(40*0.04) each edge
    assert h == 41
    assert w == 32, "4 wide would be 22 px; the 32 px floor holds it up"
    assert h > w


def test_render_puts_the_anchor_at_the_canvas_center():
    m = Model()
    m.voxel((0, 0, 0), "red")                    # the anchored voxel
    m.box((1, 0, 0), (5, 0, 0), "white")         # lopsided, all to one side
    m.box((0, 0, 1), (0, 0, 3), "white")
    w, h, rows = _decode_png(m.render(size=64, yaw=0, pitch=0,
                                      anchor=(0.5, 0.5, 0.5)))
    at = _of_hue(rows, w, 0)
    assert at, "the anchored voxel must be visible"
    rs = [r for r, _ in at]
    cs = [c for _, c in at]
    assert min(cs) < w / 2 <= max(cs), "the anchor straddles the center column"
    assert min(rs) < h / 2 <= max(rs), "and the center row"

    # Centering on a lopsided model is only useful if it does not crop it.
    border = ([(0, c) for c in range(w)] + [(h - 1, c) for c in range(w)]
              + [(r, 0) for r in range(h)] + [(r, w - 1) for r in range(h)])
    assert all(rows[r][c * 3:c * 3 + 3] == BG for r, c in border), \
        "the canvas must still hold the whole model"


def test_render_is_comparable_across_models_at_one_anchor_and_scale():
    """The point of anchor+scale: two rounds of a build, overlayable."""
    shot = dict(size=64, yaw=0, pitch=0, anchor=(0.5, 0.5, 0.5), scale=6)
    first = Model()
    first.voxel((0, 0, 0), "red")
    first.box((1, 0, 0), (3, 0, 0), "white")
    second = Model()
    second.voxel((0, 0, 0), "red")               # the same voxel, same place
    second.box((-4, 0, 0), (-1, 0, 5), "white")  # everything else moved

    wa, ha, rows_a = _decode_png(first.render(**shot))
    wb, hb, rows_b = _decode_png(second.render(**shot))
    assert (wa, ha) != (wb, hb), "different bounds, so different canvases"
    offsets = [{(r - h // 2, c - w // 2) for r, c in _of_hue(rows, w, 0)}
               for rows, w, h in ((rows_a, wa, ha), (rows_b, wb, hb))]
    assert offsets[0], "the shared voxel must be visible in both"
    assert offsets[0] == offsets[1], \
        "one anchor and one scale must put it at one offset from center"


def test_render_scale_is_pixels_per_voxel():
    m = Model()
    m.voxel((0, 0, 0), "red")
    w, _, rows = _decode_png(m.render(size=64, yaw=0, pitch=0, scale=8))
    columns = {c for _, c, _ in _pixels(rows, w)}
    assert len(columns) == 8, f"scale 8 is 8 px per voxel, not {columns}"


def test_render_of_empty_model():
    try:
        Model().render(size=16)
    except ValueError:
        return
    raise AssertionError("rendering an empty model must raise")


def test_render_centers_the_model():
    m = Model()
    m.voxel((0, 0, 0), "red")
    w, h, rows = _decode_png(m.render(size=48, yaw=30, pitch=25))
    lit = _pixels(rows, w)
    assert lit, "a voxel must leave marks on the canvas"
    assert abs(sum(r for r, _, _ in lit) / len(lit) - h / 2) < 2
    assert abs(sum(c for _, c, _ in lit) / len(lit) - w / 2) < 2


def test_render_front_view_matches_preview_orientation():
    """yaw=0 is preview's front: x to the right, z up."""
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.voxel((0, 0, 5), "blue")
    m.voxel((6, 0, 0), "green")
    w, _, rows = _decode_png(m.render(size=64, yaw=0, pitch=0))
    up = _of_hue(rows, w, 2)                       # blue, at z=5
    right = _of_hue(rows, w, 1)                    # green, at x=6
    base = _of_hue(rows, w, 0)                     # red, at the origin
    assert up and right and base
    assert max(r for r, _ in up) < min(r for r, _ in base), "+z is up-screen"
    assert min(c for _, c in right) > max(c for _, c in base), "+x is right"


def test_render_top_view_matches_preview_orientation():
    """pitch=90 is preview's top: x to the right, +y up, whatever the yaw.

    Looking straight down leaves the screen basis under-determined, so the
    camera rolls to a fixed up vector -- which is why yaw stops mattering.
    """
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.voxel((0, 6, 0), "blue")
    m.voxel((6, 0, 0), "green")
    w, _, rows = _decode_png(m.render(size=64, yaw=0, pitch=90))
    away = _of_hue(rows, w, 2)                     # blue, at y=6
    right = _of_hue(rows, w, 1)                    # green, at x=6
    base = _of_hue(rows, w, 0)                     # red, at the origin
    assert away and right and base
    assert max(r for r, _ in away) < min(r for r, _ in base), "+y is up-screen"
    assert min(c for _, c in right) > max(c for _, c in base), "+x is right"
    assert m.render(size=64, yaw=45, pitch=90) == m.render(size=64, yaw=0,
                                                          pitch=90), \
        "at the pole the camera must not roll with yaw"


def test_render_only_needs_the_surface():
    """Hollowing a model out must not change one pixel of its render.

    The renderer skips faces with a filled neighbor, which is what keeps a
    solid model from rasterizing its own interior; the skip is only sound if
    those faces could never have shown.
    """
    ball = shapes.sphere((0, 0, 0), 9)
    solid = Model()
    solid.add(ball, "red")
    shell = Model()
    shell.add(ball, "red")
    shell.keep(shell.surface())
    assert len(shell) < len(solid)
    assert shell.render(size=64) == solid.render(size=64)


def test_render_hides_what_is_behind():
    """The far voxel projects onto the same square and must not show."""
    m = Model()
    m.voxel((0, 0, 0), "red")
    m.voxel((0, 5, 0), "blue")
    w, _, rows = _decode_png(m.render(size=48, yaw=0, pitch=0))
    lit = _pixels(rows, w)
    assert lit
    assert all(p[0] > p[2] for _, _, p in lit), "only the near red may show"


def test_render_shades_faces_by_the_light():
    """One cube from a corner shows three faces, brightest one on top."""
    m = Model()
    m.voxel((0, 0, 0), "white")
    w, _, rows = _decode_png(m.render(size=48, yaw=30, pitch=30))
    lit = _pixels(rows, w)
    shades = {p for _, _, p in lit}
    assert len(shades) == 3, f"want three face shades, got {sorted(shades)}"
    rank = sorted(shades, key=sum)
    top = [r for r, _, p in lit if p == rank[-1]]
    dim = [r for r, _, p in lit if p == rank[0]]
    assert sum(top) / len(top) < sum(dim) / len(dim), \
        "the lit face is the one facing up"


def test_render_darkens_the_crease_at_a_wall():
    """Ambient occlusion: floor beside a step is darker than open floor."""
    m = Model()
    m.box((0, 0, 0), (4, 0, 0), "white")           # a strip of floor
    m.voxel((4, 0, 1), "white")                    # one block standing on it
    w, _, rows = _decode_png(m.render(size=64, yaw=0, pitch=90))
    lit = _pixels(rows, w)
    columns = {}                                   # screen column -> shades
    for _, c, p in lit:
        columns.setdefault(c, set()).add(p)
    shades = sorted({p for _, _, p in lit}, key=sum)
    assert len(shades) == 2, f"want lit and occluded floor, got {shades}"
    dim = [c for c, ps in columns.items() if ps == {shades[0]}]
    assert dim, "the occluded shade must own whole columns"
    open_floor = min(c for c, ps in columns.items() if ps == {shades[-1]})
    assert min(dim) > open_floor, "the dark strip sits beside the block"


def test_render_paints_the_background_everywhere_else():
    m = Model()
    m.voxel((0, 0, 0), "red")
    w, _, rows = _decode_png(m.render(size=32, background="black"))
    corner = bytes(parse_color("black")[:3])
    assert rows[0][:3] == corner and rows[-1][-3:] == corner


def test_render_is_deterministic():
    m = Model()
    m.sphere((0, 0, 0), 5, "red")
    assert m.render(size=48) == m.render(size=48)


def test_render_cli_writes_one_png_per_view():
    m = Model()
    m.box((0, 0, 0), (4, 4, 4), "red")
    src = _tmp()
    m.save(src)
    out = os.path.join(os.path.dirname(src), "shot.png")
    assert _main(["voxel.py", "render", src, out, "--size", "32"]) == 0
    _decode_png(open(out, "rb").read())

    # An anchor with three different components, so a mis-ordered parse shows.
    assert _main(["voxel.py", "render", src, out, "--size", "32",
                  "--anchor", "1.5,2.5,3.5", "--scale", "4"]) == 0
    assert open(out, "rb").read() == m.render(
        size=32, yaw=30, pitch=25, anchor=(1.5, 2.5, 3.5), scale=4), \
        "--anchor and --scale must reach render() as given"

    assert _main(["voxel.py", "render", src, out, "--size", "32",
                  "--view", "0,0", "--view", "-30,25.5"]) == 0
    stem = out[:-len(".png")]
    for suffix in ("_y0p0.png", "_y-30p25.5.png"):
        _decode_png(open(stem + suffix, "rb").read())


# -- scene (multi-model files) ---------------------------------------------

def _scene_shift(s):
    """The whole-chunk offset that save() applies to a scene."""
    lo = s.bounds[0]
    n = Scene.CHUNK
    return tuple(-(lo[i] // n) * n for i in range(3))


def test_scene_round_trips_through_load():
    """Negative coords and both sides of a chunk boundary survive the graph."""
    world = {(-1, 0, 0): "red", (0, 0, 0): "blue",
             (255, 3, 4): "green", (256, 3, 4): "yellow",
             (-300, -300, -300): "white", (700, 900, 1000): "orange"}
    s = Scene()
    for pos, color in world.items():
        s.voxel(pos, color)
    path = _tmp()
    s.save(path)
    back = Model.load(path)

    dx, dy, dz = _scene_shift(s)
    expect = {(x + dx, y + dy, z + dz): parse_color(c)
              for (x, y, z), c in world.items()}
    assert len(back) == len(expect)
    assert set(back.voxels) == set(expect)
    for pos, rgba in expect.items():
        assert back.palette.rgba(back.voxels[pos]) == rgba, f"color at {pos}"


def test_scene_bar_across_a_chunk_boundary_comes_back_contiguous():
    """The off-by-one that would tear the world lives at x = 255/256."""
    s = Scene()
    s.add(shapes.box((250, 0, 0), (261, 0, 0)), "metal")
    path = _tmp()
    s.save(path)
    back = Model.load(path)
    assert len(back) == 12
    assert back.size == (12, 1, 1), "no gap and no overlap at the seam"
    assert back.detached() == set()


def test_scene_reinterns_colliding_palette_indices():
    """Two models both using index 1 for different colors must not merge."""
    a = Model()
    a.voxel((0, 0, 0), "red")
    b = Model()
    b.voxel((0, 0, 0), "blue")
    assert a.voxels[(0, 0, 0)] == b.voxels[(0, 0, 0)] == 1

    s = Scene()
    s.place(a, offset=(0, 0, 0))
    s.place(b, offset=(300, 0, 0))
    path = _tmp()
    s.save(path)
    back = Model.load(path)
    assert {back.palette.rgba(i) for i in back.voxels.values()} == {
        parse_color("red"), parse_color("blue")}


def test_scene_place_keeps_no_reference_to_the_model():
    m = Model()
    m.voxel((0, 0, 0), "red")
    s = Scene()
    s.place(m, offset=(10, 0, 0))
    m.voxel((1, 0, 0), "blue")          # edited after placing
    assert len(s) == 1


def test_empty_scene_refuses_to_save():
    try:
        Scene().save(_tmp())
    except ValueError:
        pass
    else:
        raise AssertionError("empty scene should not save")


def test_scene_writes_every_chunk_at_the_full_size():
    """Uniform SIZE is what makes a center-convention slip uniform too."""
    s = Scene()
    s.voxel((0, 0, 0), "red")
    s.voxel((1000, 0, 0), "blue")       # one voxel, still a full-size chunk
    path = _tmp()
    s.save(path)
    data = open(path, "rb").read()
    sizes = [struct.unpack_from("<iii", c, 0)
             for cid, c in _walk_chunks(data, 8) if cid == b"SIZE"]
    assert sizes == [(Scene.CHUNK,) * 3] * 2


def test_scene_graph_shape_is_what_magicavoxel_expects():
    s = Scene()
    s.voxel((0, 0, 0), "red")
    s.voxel((300, 0, 0), "blue")
    path = _tmp()
    s.save(path)
    chunks = list(_walk_chunks(open(path, "rb").read(), 8))
    ids = [cid for cid, _ in chunks]
    assert ids == [b"MAIN", b"SIZE", b"XYZI", b"SIZE", b"XYZI",
                   b"nTRN", b"nGRP", b"nTRN", b"nSHP", b"nTRN", b"nSHP",
                   b"RGBA"]
    trns = [c for cid, c in chunks if cid == b"nTRN"]
    assert struct.unpack_from("<i", trns[0], 0)[0] == 0, "root is node 0"
    assert struct.unpack_from("<i", trns[0], 8)[0] == 1, "root -> the group"


def test_scene_exceeds_the_single_model_limit():
    """The whole point: a world wider than 256 that a Model could not save."""
    s = Scene()
    s.add({(x, 0, 0) for x in range(0, 1024, 64)}, "stone")
    path = _tmp()
    s.save(path)
    back = Model.load(path)
    assert back.bounds == ((0, 0, 0), (960, 0, 0))
    assert len(s.chunk_stats()) == 4, "1024 wide is four chunks"


def test_scene_bounds_size_and_chunk_stats_are_world_coordinates():
    s = Scene()
    s.voxel((-1, -1, -1), "red")
    s.voxel((300, 5, 5), "blue")
    assert s.bounds == ((-1, -1, -1), (300, 5, 5))
    assert s.size == (302, 7, 7)
    assert s.chunk_stats() == {(-1, -1, -1): 1, (1, 0, 0): 1}
    assert len(s) == 2


def test_scene_save_shifts_by_whole_chunks_only():
    """Local coordinates must be untouched, so nothing is rebinned."""
    s = Scene()
    s.voxel((-300, 7, 7), "red")        # chunk (-2, 0, 0), local (212, 7, 7)
    path = _tmp()
    s.save(path)
    back = Model.load(path)
    assert list(back.voxels) == [(212, 7, 7)], "chunk -2 became chunk 0"


def test_load_ignores_the_scene_graph_when_there_is_none():
    """Regression: a flat single-model file is still read at face value."""
    m = Model()
    m.box((0, 0, 0), (3, 3, 3), "red")
    path = _tmp()
    m.save(path)
    assert set(Model.load(path).voxels) == set(m.voxels)


# -- color arithmetic -------------------------------------------------------

def test_to_hex_round_trips_through_parse_color():
    assert to_hex("#3a6fd8") == "#3a6fd8"
    assert to_hex((10, 20, 30)) == "#0a141e"
    assert to_hex((10, 20, 30, 128)) == "#0a141e80", "alpha survives"
    assert parse_color(to_hex("blue")) == parse_color("blue")


def test_luma_and_chroma():
    assert luma("#ffffff") == 255.0
    assert luma("#000000") == 0.0
    assert chroma("#808080") == 0.0, "a grey has no chroma"
    assert chroma("#ff0000") == 255.0


def test_relight_holds_chroma_while_scale_color_does_not():
    """The distinction that cost a real bug: both move luma, one moves chroma."""
    c = "#8a5f30"
    k = 1.4
    lit, scaled = relight(c, k), scale_color(c, k)

    assert abs(luma(lit) - luma(c) * k) < 1.0, "relight scales luma by k"
    assert abs(luma(scaled) - luma(c) * k) < 1.0, "so does scale_color"

    assert abs(chroma(lit) - chroma(c)) < 1.0, "relight leaves chroma alone"
    assert abs(chroma(scaled) - chroma(c) * k) < 1.0, "scale_color drags it along"
    assert chroma(scaled) > chroma(lit) + 10, "and the two really do differ"


def test_scale_color_preserves_hue_ratios():
    assert to_hex(scale_color((100, 50, 25), 0.5)) == to_hex((50, 25, 12.5))


def test_color_ops_clamp_and_keep_alpha():
    assert parse_color(scale_color((200, 200, 200, 77), 4.0)) == (255, 255, 255, 77)
    assert parse_color(relight((10, 10, 10, 77), -5.0)) == (0, 0, 0, 77)


def test_color_ops_keep_full_precision_until_painted():
    """A color op is usually the head of a chain; rounding each step propagates.

    Scaling by 1/3 and back by 3 must return the original color. Rounding at
    each step loses it -- and the loss is one count in a channel, which is far
    too small to see and far too small for any check to catch, yet it moves
    every color derived from the result.
    """
    base = (201.47750366666637, 182.77333799999977, 48.914173041666565)
    assert to_hex(scale_color(scale_color(base, 1 / 3.0), 3.0)) == to_hex(base)

    # ramp -> integrate -> tint is the chain that caught this. The mean's
    # green lands on 182.77; rounding it to 183 before tinting shifts every
    # tint derived from it.
    ramp = [(0.0, "#f3ed9b"), (0.5, "#e4da54"), (0.8, "#c4ad13"),
            (0.95, "#9b7d02")]
    mean = disc_average(ramp)
    assert to_hex(mean) == "#c9b731"
    assert abs(mean[1] - 182.77) < 0.01, "the mean itself is not integral"
    assert to_hex(scale_color(mean, 1.02)) == "#ceba32", "the end of the chain"
    rounded_first = scale_color(parse_color(mean), 1.02)
    assert to_hex(rounded_first) != "#ceba32", \
        "rounding the mean first really does move the tint"


def test_parse_color_rounds_floats_rather_than_truncating():
    assert parse_color((10.6, 20.4, 30.5)) == (11, 20, 30, 255)
    assert parse_color((10, 20, 30)) == (10, 20, 30, 255), "ints are untouched"


def test_ramp_at_interpolates_and_clamps():
    ramp = [(0.0, (0, 0, 0)), (1.0, (100, 200, 40))]
    assert ramp_at(ramp, 0.5) == (50, 100, 20, 255)
    assert ramp_at(ramp, -3.0) == (0, 0, 0, 255), "clamped below"
    assert ramp_at(ramp, 9.0) == (100, 200, 40, 255), "clamped above"


def test_ramp_at_rejects_an_unsorted_ramp():
    try:
        ramp_at([(1.0, "red"), (0.0, "blue")], 0.5)
    except ValueError:
        return
    raise AssertionError("an unsorted ramp should raise, not silently misread")


def test_disc_average_is_area_weighted_not_a_table_average():
    """Weight goes as u, so 75% of a disc's area is outside u = 0.5."""
    ramp = [(0.0, (255, 255, 255)), (1.0, (0, 0, 0))]
    got = disc_average(ramp)[0]
    assert abs(got - 85) <= 1, "linear black-to-white ramp averages to 1/3, not 1/2"

    # Half-white/half-black by radius: the outer half is three quarters of it.
    ramp = [(0.0, (255, 255, 255)), (0.4999, (255, 255, 255)),
            (0.5, (0, 0, 0)), (1.0, (0, 0, 0))]
    assert abs(disc_average(ramp)[0] - 64) <= 2


# -- matching onto measured colors -----------------------------------------

def test_weighted_quantiles_snaps_cuts_to_whole_values():
    """A tied group must not be split, and no bucket may come out empty."""
    weights = {10: 5.0, 20: 5.0, 30: 5.0, 40: 5.0}
    assert weighted_quantiles(weights, [0.5]) == [20]
    # 0.6 sits inside the value-30 group; it snaps to a boundary either way.
    assert weighted_quantiles(weights, [0.6]) in ([20], [30])
    assert weighted_quantiles(weights, [0.25, 0.5, 0.75]) == [10, 20, 30]


def test_weighted_quantiles_respects_weight_not_count():
    """One value covering most of the picture must pull the cut onto itself."""
    weights = {1: 0.01, 2: 0.90, 3: 0.09}
    assert weighted_quantiles(weights, [0.5]) == [2]


def test_weighted_quantiles_keeps_every_bucket_non_empty():
    """The bug this was written for: two cuts landing on the same value.

    Picking each cut independently as "the boundary nearest my fraction" is
    the obvious implementation and it collapses whenever one value dominates
    or the fractions crowd together -- it returns [20, 20, 30] for the first
    case here and [0, 0, 0] for the second. A repeated cut is an empty bucket,
    which silently drops a color from the result with no other symptom.
    """
    dominant = {10: 0.05, 20: 0.60, 30: 0.15, 40: 0.15, 50: 0.05}
    assert weighted_quantiles(dominant, [0.50, 0.60, 0.80]) == [20, 30, 40]
    assert weighted_quantiles(dominant, [0.90, 0.95]) == [30, 40]

    crowded = weighted_quantiles({i: 1.0 for i in range(4)}, [0.01, 0.02, 0.03])
    assert crowded == sorted(set(crowded)) and len(crowded) == 3


def test_weighted_quantiles_raises_rather_than_emptying_a_bucket():
    try:
        weighted_quantiles({1: 1.0, 2: 1.0}, [0.2, 0.5, 0.8])
    except ValueError:
        return
    raise AssertionError("3 cuts out of 2 values must raise, not lose a color")


def test_match_histogram_reproduces_a_known_distribution():
    """Rank in, rank out: equal shares land on evenly spaced ramp midpoints."""
    ramp = [(0.0, (0, 0, 0)), (1.0, (200, 200, 200))]
    out = match_histogram({c: 1.0 for c in "abcd"}, ramp)
    assert [out[c][0] for c in "abcd"] == [25, 75, 125, 175]


def test_match_histogram_beats_a_linear_rescale_on_a_skewed_source():
    """The finding this exists for: matching by rank, not by range.

    The source is bottom-heavy -- its darkest value covers 70% of the picture
    -- while the target's brightness is spread evenly. A linear rescale of the
    source's *range* places the four values at an even 0/85/170/255 whatever
    their coverage, and paints an area-weighted mean of 51 against a target
    mean of 127.5: less than half as bright, from a mapping that has the right
    extremes, the right structure and a plausible spread.

    The rank match reproduces the target's mean exactly, and does so for any
    set of shares -- placing each value at the midpoint of the span it covers
    makes the area-weighted mean identically the target's own.
    """
    weights = {"a": 0.70, "b": 0.10, "c": 0.10, "d": 0.10}
    ramp = [(0.0, (0, 0, 0)), (1.0, (255, 255, 255))]
    out = match_histogram(weights, ramp)
    got = [out[c][0] for c in "abcd"]
    assert got == sorted(got), "rank order is preserved"

    mean = sum(weights[c] * out[c][0] for c in "abcd")
    assert abs(mean - 127.5) <= 1.0, "the target's own mean, reproduced"

    linear = dict(zip("abcd", (0, 85, 170, 255)))
    lin_mean = sum(weights[c] * linear[c] for c in "abcd")
    assert abs(lin_mean - 127.5) > 70, "what a linear rescale would have done"

    # 70% of the area is below b, so b sits at the 0.75 mark, not at 0.33.
    assert abs(got[1] - 0.75 * 255) <= 2


def test_match_histogram_preserves_the_target_mean_for_any_shares():
    ramp = [(0.0, (0, 0, 0)), (1.0, (255, 255, 255))]
    for shares in ([0.5, 0.3, 0.15, 0.05], [0.01] * 4 + [0.96], [1.0]):
        weights = {i: s for i, s in enumerate(shares)}
        out = match_histogram(weights, ramp)
        mean = sum(s * out[i][0] for i, s in weights.items()) / sum(shares)
        assert abs(mean - 127.5) <= 1.0, f"{shares} drifted off the target mean"


def test_match_histogram_uses_the_measured_distribution():
    """`distribution` bends rank onto the target's own spread."""
    ramp = [(0.0, (0, 0, 0)), (1.0, (255, 255, 255))]
    weights = {c: 1.0 for c in "ab"}
    flat = match_histogram(weights, ramp)
    # A target whose brightness is crushed into the top of the ramp.
    bent = match_histogram(weights, ramp, distribution=[(0.0, 0.8), (1.0, 1.0)])
    assert bent["a"][0] > flat["a"][0] and bent["b"][0] > flat["b"][0]


def test_match_histogram_key_orders_by_something_other_than_the_value():
    palette = {"a": "#ffffff", "b": "#000000"}
    ramp = [(0.0, (0, 0, 0)), (1.0, (255, 255, 255))]
    out = match_histogram({c: 1.0 for c in "ab"}, ramp,
                          key=lambda c: luma(palette[c]))
    assert out["b"][0] < out["a"][0], "'b' is darker, so it ranks first"


# -- noise ------------------------------------------------------------------

def test_noise_is_seeded_and_reproducible():
    assert noise3(1.5, 2.5, 3.5, 7) == noise3(1.5, 2.5, 3.5, 7)
    assert noise3(1.5, 2.5, 3.5, 7) != noise3(1.5, 2.5, 3.5, 8)
    assert all(0.0 <= noise3(i * 0.37, i * 0.11, i * 0.83, 3) <= 1.0
               for i in range(200))


def test_fbm3_is_not_uniform_so_thresholds_are_not_area_fractions():
    """The trap in the docstring, asserted: the field piles up in the middle."""
    vals = sorted(fbm3(i * 0.31, i * 0.17, i * 0.53, 5) for i in range(2000))
    p20, p50, p80 = vals[400], vals[1000], vals[1600]
    assert 0.35 < p20 < 0.45 and 0.47 < p50 < 0.57 and 0.57 < p80 < 0.67
    below = sum(1 for v in vals if v <= 0.2) / len(vals)
    assert below < 0.02, "a 0.2 threshold selects far less than 20%"


# -- rock and tube ----------------------------------------------------------

def test_rock_is_lumpy_but_stays_near_its_radii():
    c = shapes.rock((0, 0, 0), (12, 5, 5), seed=1)
    smooth = shapes.ellipsoid((0, 0, 0), (12, 5, 5))
    assert c != smooth, "a rock is not an ellipsoid"
    lo, hi = bounds(c)
    assert 20 <= hi[0] - lo[0] + 1 <= 34, "long axis stays roughly 2r"
    assert 8 <= hi[1] - lo[1] + 1 <= 15, "and the short axes stay short"


def test_rock_is_one_connected_piece_even_when_tiny():
    """At radii near a voxel the noise really does shear a corner off.

    Unfiltered, (1.2, 1.0, 0.8) comes apart on seed 53 at the default
    roughness and on 8 of 60 seeds at 0.45 -- which in a field of rubble shows
    up as phantom extra objects in the component count, not as a visible
    defect. Both ranges are swept here so the filter cannot go quiet.
    """
    for roughness in (0.30, 0.45):
        for seed in range(80):
            c = shapes.rock((0, 0, 0), (1.2, 1.0, 0.8), seed=seed,
                            roughness=roughness)
            assert len(components(c)) == 1, \
                f"seed {seed} at roughness {roughness} left a fragment"


def test_rock_is_reproducible_and_seed_dependent():
    assert shapes.rock((0, 0, 0), (6, 4, 4), seed=2) == \
        shapes.rock((0, 0, 0), (6, 4, 4), seed=2)
    assert shapes.rock((0, 0, 0), (6, 4, 4), seed=2) != \
        shapes.rock((0, 0, 0), (6, 4, 4), seed=3)


def test_tube_is_one_connected_piece_however_it_turns():
    """The guarantee: sub-voxel-free, so no corner-only join can appear."""
    paths = [
        [(0, 0, 0), (30, 7, 3)],                       # a shallow diagonal
        [(0, 0, 0), (1, 1, 1), (2, 2, 2), (3, 3, 3)],  # pure diagonal
        [(0, 0, 0), (10, 0, 0), (10, 10, 0), (10, 10, 10)],   # right angles
        [(0, 0, 0), (5, 5, 5), (-5, 5, -5), (5, -5, 5)],      # reversals
    ]
    for path in paths:
        for radius in (0, 0.5, 1, 2.5):
            c = shapes.tube(path, radius)
            assert len(components(c)) == 1, f"{path} at r={radius} came apart"


def test_tube_at_radius_zero_is_a_face_connected_thread():
    c = shapes.tube([(0, 0, 0), (9, 9, 9)], 0)
    assert len(components(c)) == 1
    assert len(components(shapes.line((0, 0, 0), (9, 9, 9)))) > 1, \
        "which is exactly what line does not give you"


def test_tube_tapers_from_end_to_end():
    c = shapes.tube([(0, 0, 0), (40, 0, 0)], 4, end_radius=0)
    at = lambda x: len({p for p in c if p[0] == x})
    assert at(0) > at(20) > at(39), "cross-section shrinks along the path"
    assert at(39) == 1, "and closes to a single voxel"


def test_tube_reaches_inside_both_endpoints():
    """Endpoints are included, which is what lets a tube be run into a shell."""
    c = shapes.tube([(0, 0, 0), (6, 0, 0)], 0)
    assert (0, 0, 0) in c and (6, 0, 0) in c


def test_model_rock_and_tube_paint():
    m = Model()
    m.rock((0, 0, 0), (4, 3, 3), "stone", seed=1)
    m.tube([(0, 0, 10), (0, 0, 20)], 1, "copper")
    assert len(m.color_histogram()) == 2


# -- coordinate-set connectivity -------------------------------------------

def test_components_of_a_coordinate_set():
    coords = shapes.box((0, 0, 0), (2, 2, 2)) | shapes.box((9, 9, 9), (10, 10, 10))
    parts = components(coords)
    assert [len(p) for p in parts] == [27, 8], "largest first"
    assert components(set()) == []


def test_components_is_face_adjacent_only():
    assert len(components({(0, 0, 0), (1, 1, 1)})) == 2, "corners do not join"


def test_model_components_still_matches_the_coordinate_set_version():
    m = Model()
    m.box((0, 0, 0), (2, 2, 2), "red")
    m.box((9, 9, 9), (10, 10, 10), "blue")
    assert m.components() == components(m.coords())
    assert len(m.detached()) == 8


# -- radial profile ---------------------------------------------------------

def test_radial_profile_reads_bands_out_in_order():
    m = Model()
    for r, color in ((4, "red"), (8, "green"), (12, "blue")):
        m.add(shapes.cylinder((0, 0, 0), r, 1) - shapes.cylinder((0, 0, 0), r - 4, 1),
              color)
    got = m.radial_profile((0, 0, 0), (1, 0, 0), (0, 0, 1), 1, 11, step=2)
    assert [c for _, c in got] == ["red", "red", "green", "green", "blue", "blue"]


def test_radial_profile_reports_a_gap_as_none():
    m = Model()
    m.add(shapes.cylinder((0, 0, 0), 12, 1) - shapes.cylinder((0, 0, 0), 6, 1),
          "red")
    got = dict(m.radial_profile((0, 0, 0), (1, 0, 0), (0, 0, 1), 0, 12, step=2))
    assert got[0] is None and got[2] is None, "the hole is reported as a hole"
    assert got[8] == "red"


def _unit3(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _tilted_annulus(normal, r_in, r_out, thickness=2):
    """A flat ring in a plane that is not axis-aligned.

    Built the way a tilted plane has to be: project along the axis the normal
    leans on most and solve the plane equation for the third component, so
    every column gets exactly one run of voxels. Sampling an inequality
    instead develops holes as the plane steepens.
    """
    n = _unit3(normal)
    a = max(range(3), key=lambda i: abs(n[i]))
    u, v = [i for i in range(3) if i != a]
    lim = int(math.ceil(r_out)) + thickness + 2
    out = set()
    for du in range(-lim, lim + 1):
        for dv in range(-lim, lim + 1):
            base = int(round(-(n[u] * du + n[v] * dv) / n[a]))
            for k in range(thickness):
                d = [0, 0, 0]
                d[u], d[v] = du, dv
                d[a] = base + k - (thickness - 1) // 2
                par = sum(d[i] * n[i] for i in range(3))
                perp2 = sum(c * c for c in d) - par * par
                if r_in * r_in <= perp2 <= r_out * r_out:
                    out.add(tuple(d))
    return out


def test_radial_profile_searches_across_a_tilted_plane():
    """A plane off the lattice lands between voxels; without the search along
    the normal, radii that are plainly filled report as empty."""
    tilt, az = math.radians(40.0), math.radians(20.0)
    normal = _unit3((math.sin(tilt) * math.sin(az),
                     -math.sin(tilt) * math.cos(az), math.cos(tilt)))
    m = Model()
    m.add(_tilted_annulus(normal, 20, 40), "red")

    e1 = _unit3(tuple(1.0 * (i == 0) - normal[0] * normal[i] for i in range(3)))
    e2 = (normal[1] * e1[2] - normal[2] * e1[1],
          normal[2] * e1[0] - normal[0] * e1[2],
          normal[0] * e1[1] - normal[1] * e1[0])
    walk = tuple(0.3 * e1[i] + 0.95 * e2[i] for i in range(3))

    holes = lambda s: [r for r, c in m.radial_profile(
        (0, 0, 0), walk, normal, 21, 39, step=1.0, search=s) if c is None]
    assert holes(0), "the unsearched probe must miss, or this proves nothing"
    assert not holes(3), "the search must find every filled radius"


def test_radial_profile_takes_the_nearest_hit_not_the_far_edge():
    """A thick disc must report the radius asked for, not the search window."""
    m = Model()
    m.add(shapes.cylinder((0, 0, -4), 20, 9), "red")
    m.add(shapes.cylinder((0, 0, 4), 20, 1), "blue")     # a lid 4 above
    got = m.radial_profile((0, 0, 0), (1, 0, 0), (0, 0, 1), 5, 15, step=5,
                           search=6)
    assert [c for _, c in got] == ["red", "red", "red"], \
        "probing outward from the plane keeps the lid out of the answer"


# -- runner ----------------------------------------------------------------

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            failures.append(name)
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
