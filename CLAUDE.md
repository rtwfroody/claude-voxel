# Working in this project

`voxel.py` writes MagicaVoxel `.vox` files. Stdlib only, no dependencies. When
asked to make a voxel object, write a script that imports it and builds the
model — don't hand-roll `.vox` bytes, and don't add dependencies.

Read `README.md` for the user-facing tour. This file is the working procedure.

## Where work goes

- **`playground/`** — new models. Gitignored, so scratch files, variants and
  huge `.vox` output cost nothing. Build here by default. See
  `playground/README.md` for the two import patterns and how to promote.
- **`examples/`** — the curated models, tracked. Something moves here only when
  it is worth reading as a demonstration of the library, with its one-off
  diagnostic scripts left behind.
- **`devscripts/`** — tools that exercise `voxel.py` itself. A checker that
  only ever applied to one model is not library tooling; it stays in that
  model's playground directory.

Don't add per-model scripts to the repo root or to `devscripts/`. That is how
the root filled up with a pelican metrics script and four dead bird builds.

## Building an object

Put each object in its own `playground/<thing>/` (or `examples/<thing>.py` once
promoted) with a `build()` returning a `Model` and a `__main__` that saves and
optionally previews. Copy the shape of `examples/omri_cake.py` — it is the most
complete example.

Rules that matter:

- **Seed any randomness** (`random.Random(SEED)`) so rebuilds are identical
  and a preview you looked at still describes the file on disk.
- **Put tunables in named constants at the top** (counts, radii, colors). The
  user usually wants to adjust one number afterwards.
- **Build around the origin using negative coordinates.** `save()` shifts the
  min corner to the file origin. Don't pre-offset everything into positive
  space by hand.

## Coordinates

Right-handed, **Z up**: `+X` right, `+Y` away from viewer, `+Z` up. `preview()`
"front" looks along `+Y`, so the front face of an object is at **minimum Y**.

## API

```
Model()                       .voxel(pos,c) .box(a,b,c) .sphere(ctr,r,c)
                              .ellipsoid(ctr,(rx,ry,rz),c) .cylinder(base,r,h,c)
                              .cone(base,r,h,c) .frustum(base,r,h,c)
                              .pyramid(base,hw,h,c) .torus(ctr,R,r,c) .line(a,b,c)
                              .wedge(a,b,c) .polygon(pts,off,h,c)
                              .helix(base,r,h,c) .rock(ctr,(rx,ry,rz),c)
                              .tube(points,r,c)
  edits                       .add(coords,c) .add_under(coords,c) .remove(coords)
                              .keep(coords) .merge(other,offset) .mirror(axis,at)
                              .translate(off) .rotate90(axis,turns) .scale(n)
                              .recolor(old,new) .center(axes) .copy()
  queries                     len() `in` .coords() .bounds .size .stats()
                              .color_histogram() .surface(facing) .support(coords)
                              .detached() .components()
                              .radial_profile(ctr,dir,normal,r_lo,r_hi)
  io                          .save(path) Model.load(path) Model.from_layers(...)
                              .preview(max_dim=48, ansi=False, views=(...))

Scene()                       .place(model,offset) .voxel(pos,c) .add(coords,c)
  CHUNK=256                   len() .bounds .size .chunk_stats() .save(path)

shapes.*                      box sphere ellipsoid cylinder cone frustum pyramid
                              torus line wedge polygon helix rock tube where
                              silhouette_hull(front,side,top)
                              -- all return set[(x,y,z)]
transforms                    translate mirror rotate90 scale bounds components
color                         parse_color to_hex luma chroma
                              scale_color(c,k)  x every channel; chroma scales too
                              relight(c,k)      +one offset; chroma held fixed
                              ramp_at(ramp,t) disc_average(ramp)
                              weighted_quantiles(weights,fractions)
                              match_histogram(weights,ramp,distribution)
noise                         noise3(x,y,z,seed) fbm3(x,y,z,seed,octaves=4)
```

`rock` is the only shape that is not smooth — a triaxial ellipsoid chewed up by
noise, for boulders, rubble and anything that shouldn't look manufactured.
`tube` sweeps a ball along a polyline and is **connected by construction**
(the spine steps one axis at a time), which `line` is not; run its endpoints
*inside* whatever it should attach to and the join cannot float either.

## Working from a reference photo

Measure colors off the reference, then move them with the operation that
matches the physics, because the two look interchangeable and are not:

- **`scale_color(c, k)`** multiplies every channel. Chroma scales with
  brightness, which is what light does — use it for albedo, shading, and
  tinting one base into a family.
- **`relight(c, k)`** adds one offset to every channel. Chroma is unchanged —
  use it when the reference's *exposure* is wrong but its color is believed.

Both scale `luma` by `k`, so only `chroma` tells them apart afterwards. Getting
this backwards turned a 1.6x brightness fix into a 1.6x saturation increase.

**Colour ops return floats and never round.** Rounding happens in exactly two
places: `parse_color` when a colour is painted, `to_hex` when it is printed.
Keep derived colours unrounded — these functions are usually the head of a
chain (integrate a ramp, then tint it into a family), and a mean channel
landing on 182.5 rounds to 182 before anything downstream sees it, dragging
every derived tint a unit low. It is invisible and no check catches it. The
testing corollary: **a helper at the head of a chain cannot be validated by
comparing its own return value — compare the end of the chain.**

`disc_average(ramp)` reduces a measured centre-to-rim ramp to the one color a
flat disc should be. Area goes as the radius, so 75% of a disc lies outside
half-radius and reading the middle of the table lands far too bright.

To reuse someone else's map or image for its *structure* while taking every
color from your own measurements, use `match_histogram` — never a linear
rescale of the source's range onto the measured range. That silently assumes
the two distributions have the same shape; when they don't, the result has the
right extremes, the right structure, a plausible spread, and is wrong. Feed it
weights that reflect real area (an equirectangular map's rows are not
equal-area — weight by cos(lat) or the poles get five times their share).

`Scene` is the way past the 256-per-axis cap: it bins world coordinates into
256³ chunks and writes a multi-model file, so 1024³ is 64 chunks. `place()`
re-interns colors and keeps **no reference** to the model, so a world can be
built one piece at a time and each piece freed. `Model.load()` reads it back
into world coordinates. Use it only when something genuinely exceeds 256 — a
single `Model` is more compatible and easier to preview.

`hollow=True, thickness=n` on box/sphere/ellipsoid/cylinder. `axis="x"|"y"|"z"`
on cylinder/cone/pyramid/torus. Colors are names from `NAMED_COLORS`, `"#rrggbb"`,
or `(r,g,b[,a])`.

## The three idioms

1. **Primitives** — `m.cylinder((0,0,0), 2, 14, "wood")`.
2. **Set algebra** — `shapes.*` return plain sets, so `sphere(c,8)-sphere(c,6)`
   is a shell and `m.remove(shapes.cylinder(...))` drills a hole. Use
   `shapes.where(a, b, predicate)` for anything the primitives don't cover.
3. **ASCII layers** — `Model.from_layers([...], {char: color})`, one string per
   Z layer bottom-up, first text row = highest Y. Use `.` for empty, never
   spaces (blank lines get trimmed and would shift geometry).

For anything on a curved surface (lettering on a cylinder), compute the surface
Y per column: `y = -int(math.sqrt((r+0.5)**2 - x**2))`, then paint that voxel
and `y-1` to sit one voxel proud. See the `OMRI` block in `omri_cake.py`.

## Verify before reporting done

The write summary ("N voxels, size ...") proves nothing about whether the
object is right. Every bug so far was invisible in it. Run these:

1. **`m.preview(views=('front',))`** — silhouette and proportion. Catches
   structural mistakes. Pick `max_dim` so the interesting part isn't
   downsampled away; the footer tells you the cell:voxel ratio.
2. **Unused-palette check** — a color defined but absent from the model means
   something got overwritten:
   ```python
   used = {m.palette.rgba(i) for i in set(m.voxels.values())}
   ```
   Interior colors legitimately won't show in a *projection*, but they should
   still be in the model.
3. **`python3 voxel.py check <file>`** (or `m.detached()`) — finds parts
   floating free. Narrow: see the trap below.
4. **Support count** for anything seated on a surface — count filled cells
   directly beneath the footprint and compare to its area:
   ```python
   n = sum(1 for p in footprint if (p[0], p[1], surface_z) in m)
   ```
5. **`python3 test_voxel.py`** after touching `voxel.py` (111 tests).

For legibility of text or fine detail, project just those voxels rather than
previewing the whole model:
```python
cells = {(x,z) for (x,y,z),i in m.voxels.items() if m.palette.rgba(i)==target}
```

## Traps that have already cost time

- **`detached()` is not a support check.** A part perched on stray decoration
  is connected and passes. The cake's candles rested on scattered sprinkles
  with most of the footprint over air, and `check` said "ok" on the broken
  build. Use the support count for "does this rest on what I meant".
- **Probe the whole footprint, not one voxel.** Sampling the centre column of
  the candles gave a confidently wrong answer about which ones were seated.
- **Running a check on the fixed model proves the model is sound, not that the
  check would have caught the bug.** To claim a check catches something, run
  it against the broken build.
- **`fbm3` is not uniform on [0, 1].** It piles up in the middle — measured
  p20 0.40, p50 0.52, p80 0.62 — so a threshold at 0.2 selects nowhere near
  20%. Thresholds picked by intuition hand most of the surface to the outer
  buckets; one body ended up with its base color on 17% of its front face.
  Sample the field over the coordinates you will actually paint and take
  `weighted_quantiles` of that. Two separate builds hit this.
- **Mirror seam.** `m.mirror("x", at=0)` reflects across `x=0`, and column 0 is
  its own mirror image. Parts crossing the centreline must start *at* `x=0`;
  starting at `x=1` leaves a one-voxel gap down the middle.
- **Ellipsoids swallow things.** An ellipsoid centred *n* above a point with
  z-radius >= *n* contains that point and will overwrite it (this hid a candle
  wick). Painting order matters: later `add` wins, `add_under` doesn't.
- **Limits.** 256 per axis and 255 colors; `save()` raises with the actual size.
  Use `Scene` if an object genuinely needs to be bigger; the 255-color limit is
  global and applies there too. Solid interiors are fine — give them a distinct
  color so a cut-away reads correctly rather than showing a uniform blob.
- **`_t` is a center, not a corner.** Anything touching the scene graph has to
  place a model's minimum corner at `_t - size // 2`. See `notes.md`.

## Housekeeping

- Log newly-solved problems in `notes.md`, including checks that *didn't* work
  and why — that file is where the traps above came from.
- Keep `README.md` accurate when the API changes; add tests to
  `test_voxel.py` for anything added to `voxel.py`.
