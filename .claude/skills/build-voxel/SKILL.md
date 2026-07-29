---
name: build-voxel
description: Step-by-step workflow for creating a voxel object or a multi-object scene as a .vox file, with or without photo references. Use whenever asked to make, build, or improve a voxel model, object, asset, or scene in this project.
---

# Building voxel objects and scenes

This is the procedure distilled from every build logged in `notes.md` (read the
relevant sections when a step cites them). `CLAUDE.md` has the API table and
the hard rules (origin-centered builds, seeded randomness, named constants,
one `examples/<thing>.py` per object); those apply throughout and are not
repeated here.

Two facts shape the whole workflow:

1. **Geometric checks and visual critique catch disjoint bug classes.** A
   model can pass connectivity, symmetry, palette, and profile checks while
   reading as the wrong animal (pelican4 round 0 passed everything and looked
   like a dodo). Conversely, a critic looking at renders will never notice a
   1-voxel mirror seam or an unsupported footprint. Always run both.
2. **Author in the representation you can proofread.** ASCII silhouettes and
   2D profiles expose topology and profile errors before any 3D exists — but
   they hide proportion errors, which only renders expose. Plan for both
   stages.

Delegation: render/critique loops read images — run them in a subagent, and
always give the critic the `.vox` path alongside the PNGs (a critic with only
renders once reviewed a single bad camera angle as seven viewpoints).

---

## Part 1 — a single object

### 1. Pick the authoring idiom for the draft

- **Organic / animal / anything curvy-asymmetric** → silhouette hull. Draw
  front/side/top ASCII masks and intersect with
  `shapes.silhouette_hull(front, side, top)`. Masks use `preview()`'s exact
  conventions (front: x right / z up; side: y right / z up; top: x right /
  y up), so a drawn mask is directly comparable to a preview later.
- **Mechanical / architectural** → primitives + set algebra
  (`shapes.* | - &`), the `omri_cake.py` / `spaceship/` style.
- **Flat or strongly layered things** → `Model.from_layers`.

These compose: hull for the body mass, primitives for parts that protrude,
carves for holes.

### 2. Proofread the 2D before building 3D

For hull masks (and it's worth sketching these profiles even for primitive
builds):

- Print the masks with a column/row ruler and look at them.
- Walk the **underside profile** (min z per column of the side mask): any step
  ≥ 4 outside a legitimate discontinuity (legs, wheels) is the "cliff" bug
  class — a sheer wall no 3D check will ever flag (notes.md, pelican2).
- Name the dorsal features you expect (e.g. crown → nape hollow → shoulder
  hump) and confirm the mask's top edge actually has them. A curve in your
  intent is not a curve in the model.
- **Proportion does not survive ASCII proofreading.** Measure the ratios that
  define the subject (bill:body length, head:body, wheel:cabin) numerically
  against the reference or known anatomy. Pelican4's bill was drawn mostly
  *under* the head; the mask "looked right" and rendered as a dodo.

Known hull limits — plan post-passes for them rather than fighting the masks:
a cross-section rectangular in two views comes out boxy (chamfer it after);
concavities invisible in all three silhouettes can't be expressed (carve
them); where two parts overlap along a view's depth axis the mask shows their
union (check that region in renders first).

### 3. Build and paint

- Geometry first, then paint by region over the built coords (later `add`
  wins — order base coats before details; `notes.md` "swallowed detail").
- Clamp every painted region on **all** axes it doesn't span. An unclamped
  x-range turned a neck stripe into a saddle across the whole back.
- Shade undersides and creases, never the lateral silhouette rim — the rim is
  exactly what every side projection shows, and rim-shading hides the main
  color from all views (notes.md, pouch).
- Keep contrast *between* feature regions, cut it *within* them; per-voxel
  banding at voxel scale reads as noise, not texture.

### 4. Geometric verification (cheap, run every rebuild)

The `CLAUDE.md` checklist: `print(m.preview(...))` (it returns, doesn't
print), `detached()`, unused-palette check, support count under anything
seated, `test_voxel.py` if `voxel.py` changed. Plus, from later builds:

- Project single colors instead of trusting the legend past 14 colors.
- For a metric you write yourself, run it against a deliberately broken build
  once — a check that can't fail is worse than no check (notes.md has four
  separate instances).
- Mirror caveat for even widths: the true plane is a half-integer;
  integer-center mirror checks false-positive.

### 5. Vision loop (the step that makes it *look right*)

In a subagent, up to ~3 rounds:

1. Render: `devscripts/render_vox.sh <vox> <outdir> 512` — 16-frame turntable
   + isometric. The thumbnailer's `-a/-d/--sunelevation` flags are silently
   ignored; the script md5-checks that frames differ — heed its warning. ASan
   stderr noise is not failure.
2. **Blind identification first**: before comparing against anything, ask
   "what is this?" of the renders (a fresh subagent is ideal). If the answer
   isn't the subject, that gap is the round's priority.
3. Critique the 2–4 biggest failures in priority order: proportion → pose →
   silhouette → color placement → surface detail. Cite model coordinates.
4. Patch, classifying each fix: mask/geometry edit vs post-pass
   (carve/repaint). Re-run the geometric suite after every patch — the two
   check families guard each other.
5. Re-render and confirm each fix visibly landed. Stop when a round produces
   only nitpicks.

---

## Part 2 — with photo references

Adds two things to Part 1: measured color, and ground truth for the critic.

1. **Fetch**: Wikimedia Commons API works well
   (`action=query&generator=search&gsrnamespace=6&prop=imageinfo&iiprop=url&iiurlwidth=1280`).
   Thumb URLs only serve standard bucket widths — 1280 works; odd widths
   return an HTML error body that lands on disk as a fake "JPEG". Check with
   `file`.
2. **Vet by looking**: keep only images clearly showing the subject in the
   pose being built (an in-flight bird is useless for a standing one; discard
   juveniles/variants). Never use an unvetted image as ground truth.
3. **Measure colors, don't guess them.** The subject's *name* is a trap — a
   "brown" pelican's body is 1–8% chroma near-neutral grey. Protocol
   (notes.md, "A brown pelican is grey"):
   - Crop small patches of the body parts you need; render a **labelled
     swatch montage** (crop image + its median hex) and *look at it* — this
     confirms position and color in one step. Identical medians from
     "different" patches mean the crop missed the subject.
   - Use **chroma** (`max(rgb)-min(rgb)`), not HLS saturation (ratio —
     near-blacks lie) and not hue below ~26 chroma (pure noise).
   - A white-balance gain far from 1.0 means the reference patch is wrong,
     not the photo.
4. Give the vision-loop critic the kept references and have it compare
   side-by-side; `devscripts/plumage_match.py` shows the shape of a
   surface-chroma regression check if one is warranted.

## Part 3 — without photo references

Substitute written knowledge for photos, and be strict about the two places
unreferenced builds go wrong:

1. **Write the spec first**: 5–10 named features with rough ratios (e.g.
   "bill ≈ 1/3 of total length, head well aft, tail low") — verbal anatomy
   is reliable; freehand proportion is not. Check the draft against these
   numbers, not against vibes.
2. **Distrust color names.** Default naturalistic surfaces to low chroma
   (< 15) with one or two deliberately saturated accents; saturated
   base-coats are the #1 tell of an unreferenced build.
3. Run the same vision loop; the **blind identification** test carries the
   weight ground-truth photos would ("does a fresh viewer say pelican?").
4. If realism ends up mattering, stop and fetch references — measuring beats
   iterating on guesses (the khaki→grey lesson cost a full rebuild).

---

## Part 4 — a scene of multiple objects

1. **Stay in one `Model` unless the scene genuinely exceeds 256 per axis**;
   then use `Scene` (255-color limit is global either way). Budget the
   palette across objects *before* building.
2. **Terrain/ground first.** Skin height fields by filling each column down
   to its lowest 4-neighbour — a 1-voxel skin is a sieve and detaches
   (notes.md, fountain). Restrict slope-shading to real steps (drop ≥ 2) or
   the whole surface flattens.
3. **Build each object as its own module** with `build()` returning a
   `Model`, verified per Part 1, then `merge`/`place` with offsets. Keep a
   layout table (name → offset → footprint) in one place.
4. **Seating is the scene-level bug class.** For every placed object, count
   filled ground cells directly beneath its footprint vs footprint area.
   `detached()` passes things perched on stray decoration; the support count
   is what catches them. Probe whole footprints, not center voxels.
5. **If objects must connect** (modular kits): fix the attachment convention
   first and smoke-test it — orient one test box onto every socket/facing
   and check bounds — before authoring any real asset. Score the *contract*
   region (the mount plate), not the whole face. (notes.md, spaceship.)
6. **Weathering/greebling passes are destructive**: restrict them to masked
   regions, run them before emissive/legible detail, and re-paint the one
   feature that makes each shape readable afterwards. The unused-palette
   check catches a pass that ate a color.
7. Scene verification: per-object checks still apply; plus project specific
   colors (never the legend — it wraps at 14), use half-section previews for
   anything sunken or interior (`keep({p for p in coords if p[1] >= 0})`),
   and remember a projection answers questions about the *nearest* voxel
   only.
8. Vision loop on the assembled scene: same protocol, but critique
   **composition** first (relative scale between objects, sightlines, does
   the focal object read from the default view), then per-object issues.
   Relative-scale errors are invisible in per-object renders and glaring in
   the assembled one.
9. Parallel builds: one owner writes each shared file (manifest, notes);
   workers report back. A filtered build must not write a shared manifest.

---

## Done means

Geometric suite green **and** a final-round render critique with nothing but
nitpicks **and** (if references exist) colors within measured tolerance. Then
log anything newly learned in `notes.md` — including checks that didn't work
and why.
