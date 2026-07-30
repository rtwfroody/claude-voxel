# voxel.py

Author MagicaVoxel `.vox` files from Python. Single file, no dependencies,
stdlib only.

```python
from voxel import Model

m = Model()
m.cylinder((0, 0, 0), 2, 14, "wood")
m.sphere((0, 0, 20), 8, "leaf")
print(m.preview())          # check it in the terminal
m.save("tree.vox")
```

## Layout

| file | what |
| --- | --- |
| `voxel.py` | the whole toolkit: model, shapes, palette, reader, writer, preview, CLI |
| `test_voxel.py` | 109 tests; `python3 test_voxel.py` (also runs under pytest) |
| `examples/demo.py` | three worked builds showing the three authoring idioms |
| `examples/` | the curated models that are worth keeping — read these |
| `playground/` | scratch space for building new models; gitignored, see its README |
| `devscripts/` | tools that exercise `voxel.py` itself (scene smoke test, renderer) |

New models start in `playground/`, which is gitignored so nothing there clutters
the repo. When one turns out to be worth keeping, move it into `examples/`.

## Coordinates

Right-handed, **Z up**, matching MagicaVoxel: `+X` right, `+Y` away from the
viewer, `+Z` up.

Models are sparse and accept **negative coordinates** while you build, so you
can centre things on the origin and build symmetric objects around `x = 0`.
`save()` shifts the min corner to the file origin and enforces the format's
256-per-axis limit.

## The three idioms

**1. Primitives.** `box sphere ellipsoid cylinder cone frustum pyramid torus
wedge polygon helix line rock tube voxel`, each taking a color as the last
positional argument:

```python
m.box((-8, -8, -10), (8, 8, -8), "stone")
m.cone((0, 0, 0), 6, 12, "red", axis="z")
m.add_under(shapes.sphere((0, 0, 20), 8), "leaf")   # only fill empty space
```

Four of these carry more weight than the rest when you are building something
that isn't made of blocks:

```python
m.frustum((0, 0, 0), 6, 10, "metal", top_radius=3)   # taper, not a full cone
m.wedge((-4, 0, 0), (4, 20, 6), "hull", axis="y", taper="z")   # ramp / nose
m.helix((0, 0, 0), 5, 20, "copper", turns=4, thickness=2)      # coil
```

`polygon` extrudes a closed 2D outline, which beats stacking boxes for
anything with a swept profile — draw the plan view, extrude it:

```python
plan = [(0, -16), (3, -6), (6, 8), (0, 10)]         # (x, y) pairs
m.polygon(plan, -1, 3, "hull")                      # 3 layers, from z = -1
```

Fill is even-odd and the outline is always kept, so a spike thinner than a
voxel still survives.

`rock` is the one primitive that isn't smooth — a triaxial ellipsoid chewed up
by seeded 3-D noise, for boulders, rubble, asteroids, anything that shouldn't
look manufactured. `tube` sweeps a ball along a polyline:

```python
m.rock((0, 0, 0), (14, 5, 5), "stone", seed=7)          # a 3:1 lump
m.tube([(0, 0, 0), (0, 6, 14), (0, 20, 4)], 3, "lava", end_radius=0)
```

Shape is an input to `rock` rather than just size, because a 3:1 lump and a
round one read as different objects. Same seed, same rock, every time.

`tube` is **connected by construction**: its spine steps one axis at a time,
so no two stamps can meet at a corner however sharply the path turns or however
coarsely you sampled it. `line` gives no such guarantee — at thickness 1 it
can leave a diagonal break. That extends to joining onto something else, using
the same trick: run the endpoints *inside* the target rather than onto its
surface, and the connection cannot miss by a voxel and float.

**2. Set algebra.** Everything in `shapes.*` returns a plain `set` of
`(x, y, z)` tuples, so `|`, `-` and `&` compose them before you paint:

```python
from voxel import shapes
shell = shapes.sphere((0, 0, 0), 8) - shapes.sphere((0, 0, 0), 6)
m.add(shell, "glass")
m.remove(shapes.cylinder((0, 0, -10), 2, 20))       # drill a hole
```

`shapes.where(a, b, predicate)` is the escape hatch for anything else:

```python
m.add(shapes.where((-20, -20, 0), (20, 20, 0),
                   lambda x, y, z: (x * x + y * y) % 7 < 2), "sand")
```

Transforms and queries on coordinate sets: `translate mirror rotate90 scale
bounds components` — `components(coords)` checks a generated shape for having
come apart before it is ever painted, which is where it is cheapest to catch. The
model carries its own colored versions — `m.translate() m.rotate90() m.scale()
m.mirror() m.center()` — so you can build a part once and orient copies of it:

```python
gun = build_gun()                       # points along -y
m.merge(gun.copy().rotate90("z", 1), offset=(10, 0, 0))   # now points along -x
```

`m.recolor(old, new)` repaints one color throughout, which is how one mesh
becomes a whole faction's worth of variants.

`m.surface()` returns the voxels with an exposed face, optionally filtered to
one direction, for skinning a solid shape:

```python
m.add(m.surface("z+"), "light_grey")    # top-lit highlight
m.add(m.surface("z-"), "dark_grey")     # underside shadow
```

**3. ASCII layers.** One string per Z layer, bottom first. Within a layer the
first text row is the **highest Y**, so each layer reads as a top-down plan:

```python
Model.from_layers(["wwwww\nw...w\nwwwww", "..r..\n.rrr.\n..r.."],
                  {"w": "sand", "r": "dark_red"})
```

Use `.` for empty, not spaces — leading/trailing blank lines are trimmed, so a
row of spaces would shift the geometry. Unmapped characters raise rather than
being silently skipped.

## Color, and working from a reference

Colors are names from `NAMED_COLORS`, `"#rrggbb"` or `(r, g, b[, a])`.
`parse_color` is the canonical form every function here returns, and `to_hex`
is the printable one. `luma` is perceived brightness, `chroma` is
colorfulness.

Two ways to change a color's brightness, which look interchangeable and are
not. Both scale `luma` by `k`; only `chroma` tells them apart afterwards:

```python
scale_color("#8a5f30", 1.4)   # x every channel -- chroma scales along with it
relight("#8a5f30", 1.4)       # +one offset    -- chroma unchanged
```

Multiplying is what light physically does, so `scale_color` is the honest way
to move a *material's* albedo, and tints of one base still read as one
substance. `relight` is an exposure correction: use it when a reference photo's
brightness is known wrong but its colorfulness is believed. Reaching for the
wrong one is silent — it yields a plausible color of the right brightness —
and has already turned a 1.6x brightness fix into a 1.6x saturation increase.

A ramp is `[(position, color), ...]`, ascending — a gradient sampled wherever
you could read a value off the reference. `ramp_at` reads a color out of one;
`disc_average` reduces a centre-to-rim ramp to the single color a flat disc
should be:

```python
ramp_at(limb, 0.5)      # the color halfway out
disc_average(limb)      # the color the whole disc averages to
```

Those differ more than you would expect, and the second is the one you want.
Area goes as the radius, so 75% of a disc lies outside half-radius; reading
the middle of the table lands far too close to the centre color.

To reuse someone else's map, image or heightfield for its **structure** while
taking every color from your own measurements, match histograms:

```python
weights = {"a": 0.70, "b": 0.10, "c": 0.10, "d": 0.10}   # area each covers
colors = match_histogram(weights, measured_ramp)
```

Every source value is placed at the measured brightness that has the same
fraction of the picture below it. The alternative that looks equivalent —
rescaling the source's range linearly onto the measured range — assumes the
two distributions have the same *shape*, and when they don't the result has
the right extremes, the right structure, a plausible spread, and is wrong. On
the skewed example above it paints a mean brightness of 51 against a target of
127.5, and passes every other check.

`weighted_quantiles(weights, fractions)` is the bucketing version, for when
the target is N colors rather than a ramp. Its cuts snap to whole value
boundaries: a quantised source holds only a handful of distinct values, and a
cut placed at the exact requested fraction lands *inside* a tied group of
them, which either dithers or silently empties the bucket above it.

Feed both of them weights that reflect real area. Cell counts are only area
when the cells are equal-area — an equirectangular map's rows are not.

## Noise

`noise3(x, y, z, seed)` and `fbm3(...)` are seeded value noise in `[0, 1]`,
pure functions of their arguments, so a rebuild is identical without anyone
having to remember to reseed. They are what `rock` is carved with, and what
you mottle a flat surface with.

**`fbm3` is nowhere near uniform.** It piles up in the middle — measured
p20 0.40, p50 0.52, p80 0.62 — so a threshold at 0.2 selects far less than
20%, and thresholds picked as if it were uniform hand most of a surface to the
outer buckets. If the coverage matters, sample the field over the coordinates
you are actually going to paint and take its quantiles:

```python
vals = [fbm3(x * s, y * s, z * s, seed) for x, y, z in coords]
cuts = weighted_quantiles({v: 1 for v in vals}, [0.25, 0.75])
```

## Silhouettes

`shapes.silhouette_hull` goes the other way from `preview()`: draw two or
three orthographic masks and it keeps the solid that casts all of those
shadows.

```python
front = ["..##..", ".####.", "######", "######", "######"]   # x right, z up
side = ["..##..", ".####.", "######", "######", "######"]    # y right, z up
m.add(shapes.silhouette_hull(front=front, side=side), "wood")  # hipped roof
```

The masks read exactly like `preview()` output — front is x right and z up,
side is y right and z up, top is x right with **+y in the first row** — so a
drawing and a preview of what it built are directly comparable. `.` and space
are empty, rows may be ragged, and the hull is anchored at the origin.

Two masks are the minimum; one on its own leaves an axis unbounded. Views that
pin the same extent (front and top both give x) must agree on it or it raises.
The result is the *largest* solid matching every drawing, so an interior drawn
hollow in two views leaves only the corners where the two fills overlap.

## Symmetry

`Model.mirror(axis, at)` reflects the model and keeps both halves — build one
side of a creature or vehicle and the halves can't drift apart.

One gotcha: column `at` is its own mirror image, so parts meant to cross the
centreline must start **at** the mirror plane. Building at `x >= 1` and
mirroring across `x = 0` leaves a one-voxel seam down the middle.

## Preview

`m.preview()` prints orthographic front/side/top projections with a color
legend, downsampling to `max_dim` for large models. `preview(ansi=True)` gives
truecolor blocks. There's a CLI for files:

```sh
python3 voxel.py preview examples/robot.vox --ansi
python3 voxel.py info examples/robot.vox
```

## Connectivity

Assembled models fail by having a part float away from what it should rest on,
which is invisible in the voxel count and easy to miss in a projection.

```python
assert m.detached() == set()      # empty means one solid piece
m.components()                    # connected components, largest first
```

```sh
python3 voxel.py check examples/omri_cake.vox   # exit 1 and a report if not
```

`detached()` floods from the lowest voxel (the base, for anything standing on
a plate) using **face adjacency**, so parts meeting only at an edge or corner
count as detached.

It answers "is anything floating free?", which is narrower than it sounds: a
part perched on a stray decoration is connected, and passes. For "does this
rest on what I meant", use `support()`, which counts how many of a footprint's
voxels actually have something under them:

```python
seated, total = m.support(footprint)        # offset defaults to (0, 0, -1)
assert seated == total
```

Probe the **whole** footprint, not a sample: checking the centre column of a
part gives a confidently wrong answer about whether it is seated.

For anything built in radial layers — a ring system, a tree stump, a
dartboard, a gasket — `radial_profile` walks outward through a plane and
reports the color at each radius:

```python
m.radial_profile((0, 0, 0), direction, normal, r_lo=20, r_hi=40)
# [(20.0, 'stone'), (21.0, 'stone'), (22.0, None), ...]
```

This is the check a voxel count cannot make. "31,000 voxels" is equally true
of a ring system with its gap in the wrong place, its bands in the wrong order,
or its gap painted black; a radial color walk is false of all three. Each
radius is probed a few voxels along the normal, working outward, because a
plane that isn't axis-aligned lands between lattice points — without that,
plainly filled radii report as empty.

## Worlds bigger than 256³

A `Model` is capped at 256 voxels per axis, because that is all one
`SIZE`/`XYZI` pair can address. `Scene` lifts the cap by binning voxels into
256³ chunks, writing one model per chunk and a scene graph that positions
them:

```python
from voxel import Scene

s = Scene()
for i in range(4):
    tower = build_tower(i)
    s.place(tower, offset=(i * 300, 0, 0))
    del tower                      # the scene copied what it needed
print(s.save("world.vox"))         # 1024³ is just 64 chunks
```

`place()` re-interns colors by RGBA, so the models you feed it can carry any
palette, and it keeps **no reference** to the model — that is the point, so a
world larger than memory can be built one piece at a time and each piece freed
as it lands. `voxel()` and `add()` paint straight into the scene.

Coordinates are world coordinates and may be negative; `save()` shifts the
lowest occupied chunk to chunk `(0,0,0)`. Chunks with nothing in them are never
written. `bounds`, `size`, `len()` and `chunk_stats()` report on the world.

## Format notes

Single models are written as version 150 with a flat
`MAIN → SIZE / XYZI / RGBA` layout, the maximally compatible form — every
`.vox` loader reads it. `Model.save()` raises with the actual size if you
exceed 256 per axis or 255 colors.

`Scene.save()` adds the scene-graph chunks (`nTRN`/`nGRP`/`nSHP`). Two things
about them are worth knowing, because both are easy to get subtly wrong:

- A transform's `_t` is the position of the model's **center**, not its minimum
  corner. The corner lands at `_t - size // 2`, floored.
- Every chunk is written at the full 256³ `SIZE` even when nearly empty. `SIZE`
  is 12 fixed bytes and `XYZI` stores only filled voxels, so this is free — and
  uniform sizes mean a mistake in the center convention slides the whole world
  instead of tearing chunks apart at their seams.

`Model.load()` reads both layouts, applying scene-graph translations, so
`Scene.save()` round-trips back to world coordinates. Rotations (`_r`) are
ignored — we never write one, but foreign files use them freely.

The format's notorious off-by-one is handled in `Palette.chunk_bytes`: entry
*i* of the 256-entry RGBA table is palette index *i+1*, so index 0 means empty
and the last table slot is unreachable padding.
