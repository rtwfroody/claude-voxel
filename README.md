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
| `test_voxel.py` | 66 tests; `python3 test_voxel.py` (also runs under pytest) |
| `examples/demo.py` | three worked builds showing the three authoring idioms |
| `spaceship/` | a modular-spaceship game asset pack built on this library |

## Coordinates

Right-handed, **Z up**, matching MagicaVoxel: `+X` right, `+Y` away from the
viewer, `+Z` up.

Models are sparse and accept **negative coordinates** while you build, so you
can centre things on the origin and build symmetric objects around `x = 0`.
`save()` shifts the min corner to the file origin and enforces the format's
256-per-axis limit.

## The three idioms

**1. Primitives.** `box sphere ellipsoid cylinder cone frustum pyramid torus
wedge polygon helix line voxel`, each taking a color as the last positional
argument:

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

Transforms on coordinate sets: `translate mirror rotate90 scale bounds`. The
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
