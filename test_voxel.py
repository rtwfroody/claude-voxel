"""Tests for voxel.py. Run directly (python3 test_voxel.py) or under pytest."""

import os
import struct
import sys
import tempfile

from voxel import (MAX_DIM, Model, Palette, Scene, bounds, mirror, parse_color,
                   rotate90, scale, shapes, translate, _walk_chunks)


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
