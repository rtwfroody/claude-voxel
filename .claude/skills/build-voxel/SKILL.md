---
name: build-voxel
description: Step-by-step workflow for creating a voxel object or a multi-object scene as a .vox file, with or without photo references. Use whenever asked to make, build, or improve a voxel model, object, asset, or scene in this project.
---

# Building voxel objects and scenes

This is the procedure distilled from every build logged in `notes.md` (read the
relevant sections when a step cites them). `CLAUDE.md` has the API table and
the hard rules (origin-centered builds, seeded randomness, named constants,
one directory or script per object); those apply throughout and are not
repeated here.

**Build in `playground/<thing>/`**, which is gitignored — a build in progress
generates one-off diagnostic scripts and large `.vox` files that should not
land in the repo. `playground/README.md` covers the import patterns and how to
promote a finished model to `examples/`.

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

Any delegated stage should **write its output to disk as it goes** — a partial
build script or a half-filled measurements file is recoverable; work held only
in a subagent's context is not. Agents killed by transient API errors cannot be
resumed (`SendMessage` returns "No transcript found"), so relaunching against
surviving files on disk is the only recovery. One reference pass lost its
complete vetting decisions this way, twice.

Image budget, everywhere in this document: **nothing you look at should exceed
a long edge of ~768 px or roughly a megabyte.** A voxel model is 100–250 voxels
across, so higher resolution carries no information the build can use, and big
images actively break runs — see Part 2 step 2, where multi-megabyte contact
sheets killed a reference pass twice. Renders at 512 (Part 1 step 5) are
already the right size; downscale fetched photos and any crop before viewing.

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
   + isometric. Needs `vengi-thumbnailer` on PATH (or `THUMB=/path/to/it`);
   the script says so and exits 1 if it is missing. The thumbnailer's
   `-a/-d/--sunelevation` flags are silently ignored; the script md5-checks
   that frames differ — heed its warning. ASan stderr noise is not failure.
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
   `file` on every download and delete the fakes before going further.
2. **Downscale immediately, before looking at anything.** Fetch at 1280 (the
   bucket width that works), then resize every keeper to a **long edge of 768
   px**, JPEG quality 85, and work only from those. Build **contact sheets at
   400 px tiles, 6 per sheet** for vetting.

   ```python
   from PIL import Image
   import glob, os
   os.makedirs('small', exist_ok=True)
   for f in sorted(glob.glob('raw/*')):
       im = Image.open(f).convert('RGB')
       im.thumbnail((768, 768))                     # long edge, aspect preserved
       im.save('small/%s.jpg' % os.path.basename(f)[:2], quality=85)
   ```

   Two reasons, one of which is not obvious:

   - **The model can't use the resolution.** A voxel object is 100–250 voxels
     across. A 768 px reference is already 3–7× oversampled for every ratio
     and every color patch you will take off it; 1280 px and up buys nothing.
   - **Large image reads destabilise the run.** A reference pass on 22 photos
     died twice to server-side API errors while handling 1.4–1.8 MB contact
     sheets, losing all its work both times. At 400 px tiles the same sheets
     are ~70 KB. Keep anything you *view* well under a megabyte: downscale
     crops below ~700 px before looking at them, and keep swatch montages to
     a few hundred px.

   Sampling color off the 768 px copy is fine — downscaling averages pixels,
   so a patch median barely moves. Full-resolution originals are worth opening
   only for a measurement that genuinely needs the pixels; keep them on disk
   but out of the loop.
3. **Checkpoint measurements to a file as you go**, appending after each one
   rather than reporting at the end. The two crashes above cost a complete set
   of vetting decisions and ratios that were only ever held in context. Make
   the notes file the deliverable and the final reply a summary of it.
4. **Vet by looking**: keep only images clearly showing the subject in the
   pose being built (an in-flight bird is useless for a standing one; discard
   juveniles/variants). Never use an unvetted image as ground truth. Note the
   viewing angle — a subject angled away from the camera foreshortens, and
   averaging a foreshortened length in with a side-on one silently shrinks it.
5. **Measure colors, don't guess them.** The subject's *name* is a trap — a
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
6. Give the vision-loop critic the kept references and have it compare
   side-by-side. If a surface-chroma regression check is warranted, key it on
   the **area-weighted median chroma of the visible surface** (depth-tested
   from three views) rather than a raw voxel count — see notes.md, "A brown
   pelican is grey".

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
