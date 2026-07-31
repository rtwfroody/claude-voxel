---
name: build-voxel
description: Step-by-step workflow for creating a voxel object or a multi-object scene as a .vox file, with or without photo references. Use whenever asked to make, build, or improve a voxel model, object, asset, or scene in this project. Accepts an optional leading quality tier (draft | standard | fine | ultra).
argument-hint: "[draft|standard|fine|ultra] <what to build>"
---

# Building voxel objects and scenes

This is the procedure distilled from the builds that came before. `CLAUDE.md`
has the API table and the hard rules (origin-centered builds, seeded
randomness, named constants, one directory or script per object); those apply
throughout and are not repeated here.

**Build in `playground/<thing>/`**, which is gitignored — a build in progress
generates one-off diagnostic scripts and large `.vox` files that should not
land in the repo. `playground/README.md` covers the import patterns and how to
promote a finished model to `examples/`.

**Record the prompt in the build script.** The main Python file — the one with
`build()` in it — opens with a module docstring quoting the user's request
verbatim, before any import:

```python
"""Chess knight, voxel model.

Prompt:
    "make me a chess knight, stylized game asset, about 40 voxels tall"

Follow-ups:
    "the mane reads as a fin -- break it into locks"
"""
```

Quote it, don't paraphrase: the wording is the specification, and a summary
loses exactly the constraint that gets argued about later. Append each
follow-up that changed the model as its own line, so the docstring stays the
record of what was actually asked for across the whole build. A multi-module
build puts this in the entry point (`build.py`) only; the other modules just
say what they contain.

## Quality tiers

The first word of the arguments may be a quality tier: `draft`, `standard`,
`fine`, or `ultra`. Default is **standard**. If the prompt's own wording
implies a tier ("quick and rough" → draft; "polish it", "photoreal" → fine or
ultra), use that, and say in the reply which tier was picked and why.

The tier scales **visual** effort only. Everything engineering-grade is
unconditional at every tier: seeded randomness, named constants, the geometric
suite (Part 1 step 4), support counts, and the prompt-recording docstring.
Draft means fewer renders, not sloppier construction.

| | draft | standard | fine | ultra |
| --- | --- | --- | --- | --- |
| style (step 0) | pick one, record it | ask | ask + `spec.md` | ask + `spec.md` |
| color source | recalled, chroma-disciplined (Part 3) | recalled (Part 3) | measured from photos (Part 2) | measured from photos (Part 2) |
| vision loop (step 5) | none — ASCII previews only | 1 round, spine only | ≤3 rounds, spine + subject views | ≤3 rounds, spine + subject + sweep |
| harvest pass | skip | skip | run | run |

Rendering is `m.render()` / `python3 voxel.py render` — in-repo, stdlib, no
external tools — so each round renders exactly the views it needs. Views come
in three kinds:

- **The spine** — fixed, identical for every build: two blind-ID heroes,
  `--view 35,25 --view 215,25`, plus the canonical orthographics
  `--view 0,0 --view 90,0 --view 0,90`, which match `preview()` and the
  silhouette-mask conventions exactly (lay them straight over the masks).
  The spine is fixed *so that blind identification cannot be aimed*: if the
  build picks every camera, "what is this?" quietly becomes "identify this
  from its best angle".
- **Subject views** — chosen in step 0's spec, one per defining feature: a
  low side view for a bill profile, a top view for lettering, a **negative
  pitch for anything with an underside or overhang** — undersides are a
  standing bug class here and now have a camera that can see them.
- **Confirmation views** — chosen per fix in step 5.5, aimed at the patched
  region and usually cropped to it.

Ultra adds a completeness sweep: an 8-frame turntable (`--view y,25` for y in
0,45,…,315) plus one underside (`--view 35,-30`). Blind identification
(step 5.2) applies at every tier that renders, and always sees the spine
heroes of the **whole** model — never a builder-chosen or cropped view.

## Step 0 — agree the visual style before building anything

**There is no house style.** The models in this repo do not share one, and
nothing in `CLAUDE.md` implies a default. Do not infer one from
`examples/` — `omri_cake.py` is a reference for *code* structure (build(),
named constants, seeded randomness), not for how a model should look.

So the first action of a build, before any spec, palette or geometry, is to
**ask the user which style to work in** — one `AskUserQuestion` with a short
menu. (At the draft tier, skip the question: pick the style that fits the
subject and state the choice instead.) Always include "match an existing
`.vox`" as one of the options. A reasonable menu:

- **Match an existing model** — the user names a `.vox` (or a directory in
  `playground/`); measure it and match. Procedure below.
- **Naturalistic / measured** — real-world colors sampled or recalled at real
  chroma, subtle value shading, restrained palette. Photo references expected
  (Part 2). This is the `pelican5` end of the spectrum.
- **Stylized game asset** — bold saturated palette, flat regions, features
  exaggerated for legibility at small size, readable silhouette over realism.
- **Toy diorama** — chunky forms, few colors per object, oversized
  characteristic features, deliberately low detail density.

Offer the two or three that actually fit the subject rather than all four, and
say which you would pick. If the user has already stated a style in the prompt
("make it look like a pixel-art game", "photoreal-ish"), skip the question and
record the choice instead.

Style is not decoration — it fixes decisions that are expensive to reverse
later, so pin all of these down and write them into the build's `spec.md`
before the first line of geometry:

| decision | why it must be settled first |
| --- | --- |
| voxels per metre | every prop's size table derives from it |
| palette size and chroma ceiling per material class | a repaint late is a rebuild |
| shading strategy (none / underside only / full value ramp) | affects how geometry is authored |
| detail density (voxels per feature) | decides whether a fruit is 1 voxel or 4 |
| outline or rim treatment, if any | interacts with the rim-shading trap |

**To match an existing model**, measure it rather than eyeballing it:

```python
m = Model.load(path)
print(m.stats())                                  # count, size, colors used
for name, n in m.color_histogram():               # (color name, voxels), desc
    r, g, b, _ = parse_color(name)
    print(f"{name} {n:7d}  chroma {max(r,g,b)-min(r,g,b):3d}")
```

Take the chroma (`max(rgb) - min(rgb)`) and luma distribution over the
histogram *weighted by voxel count* — an unweighted palette read overstates
accent colors that occupy twenty voxels. Note voxels-per-feature on something
identifiable, whether shading exists at all, and how many colors a single
material uses. Render it (`Model.load(path).render(...)`, step 5) and look at
it. Then write those
numbers into `spec.md` as the target, and check the new model against them at
the end.

Two facts shape the whole workflow:

1. **Geometric checks and visual critique catch disjoint bug classes.** A
   model can pass connectivity, symmetry, palette, and profile checks while
   reading as the wrong animal — one bird draft passed every geometric check
   and read as a dodo. Conversely, a critic looking at renders will never
   notice a 1-voxel mirror seam or an unsupported footprint. Always run both.
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
  (`shapes.* | - &`), the `examples/omri_cake.py` style.
- **Flat or strongly layered things** → `Model.from_layers`.

These compose: hull for the body mass, primitives for parts that protrude,
carves for holes.

### 2. Proofread the 2D before building 3D

For hull masks (and it's worth sketching these profiles even for primitive
builds):

- Print the masks with a column/row ruler and look at them.
- Walk the **underside profile** (min z per column of the side mask): any step
  ≥ 4 outside a legitimate discontinuity (legs, wheels) is the "cliff" bug
  class — a sheer wall no 3D check will ever flag.
- Name the dorsal features you expect (e.g. crown → nape hollow → shoulder
  hump) and confirm the mask's top edge actually has them. A curve in your
  intent is not a curve in the model.
- **Proportion does not survive ASCII proofreading.** Measure the ratios that
  define the subject (bill:body length, head:body, wheel:cabin) numerically
  against the reference or known anatomy. One bird draft had its bill drawn
  mostly *under* the head; the mask "looked right" and rendered as a dodo.

Known hull limits — plan post-passes for them rather than fighting the masks:
a cross-section rectangular in two views comes out boxy (chamfer it after);
concavities invisible in all three silhouettes can't be expressed (carve
them); where two parts overlap along a view's depth axis the mask shows their
union (check that region in renders first).

### 3. Build and paint

- Geometry first, then paint by region over the built coords (later `add`
  wins — order base coats before details, or a later pass swallows them).
- Clamp every painted region on **all** axes it doesn't span. An unclamped
  x-range turned a neck stripe into a saddle across the whole back.
- Shade undersides and creases, never the lateral silhouette rim — the rim is
  exactly what every side projection shows, and rim-shading hides the main
  color from all views.
- Keep contrast *between* feature regions, cut it *within* them; per-voxel
  banding at voxel scale reads as noise, not texture.

### 4. Geometric verification (cheap, run every rebuild)

The `CLAUDE.md` checklist: `print(m.preview(...))` (it returns, doesn't
print), `detached()`, unused-palette check, support count under anything
seated, `test_voxel.py` if `voxel.py` changed. Plus, from later builds:

- Project single colors instead of trusting the legend past 14 colors.
- For a metric you write yourself, run it against a deliberately broken build
  once — a check that can't fail is worse than no check. This has gone wrong
  more often than any other verification mistake.
- Mirror caveat for even widths: the true plane is a half-integer;
  integer-center mirror checks false-positive.

### 5. Vision loop (the step that makes it *look right*)

Skipped at the draft tier — the ASCII proofread of step 2 and the previews of
step 4 are draft's whole visual check. Rounds and view counts per tier are in
the quality-tier table; standard stops after one round unless blind
identification fails.

In a subagent, up to ~3 rounds:

1. Render the round's views (the tier table says which):
   `python3 voxel.py render <vox> --view YAW,PITCH ...`, or `m.render()` from
   the build script. **On the first rendering round, pin the camera**: pick an
   anchor (the model's center at first draft — a world point; a voxel's
   center is `p + 0.5`) and a scale (px per voxel; whatever puts the long
   edge near 512), record both as named constants in the build script, and
   pass them to every render for the rest of the build. Same anchor + same
   scale = every round's images center-aligned and pixel-comparable, even as
   the bounds shift under fixes. Size rules: 512 default, 768 the hard
   ceiling — a model is 100–250 voxels across, so anything past that pays
   vision tokens for no information.
2. **Blind identification first**: before comparing against anything, ask
   "what is this?" of the spine heroes (a fresh subagent is ideal). If the
   answer isn't the subject, that gap is the round's priority.
3. Critique the 2–4 biggest failures in priority order: proportion → pose →
   silhouette → color placement → surface detail. Cite model coordinates.
4. Patch, classifying each fix: mask/geometry edit vs post-pass
   (carve/repaint). Re-run the geometric suite after every patch — the two
   check families guard each other.
5. Re-render and confirm each fix visibly landed, with a view **aimed at the
   patched region** (name the yaw/pitch that shows it when writing the fix).
   If the feature sits below ~4 px/voxel in the full render, zoom by
   rendering the region, not by enlarging the canvas:
   `m.copy().keep(region).render(...)` — a before/after pair of region
   renders shares its own anchor/scale. Region renders are for confirmation
   only, never identification; the critic judging "what is this" always sees
   the whole model. Stop when a round produces only nitpicks.

---

## Part 2 — with photo references

Fine and ultra tiers (or any tier where the user supplies photos). Adds two
things to Part 1: measured color, and ground truth for the critic.

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
   "brown" pelican's body is 1–8% chroma near-neutral grey. Protocol:
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
   from three views) rather than a raw voxel count — a count says nothing
   about what the eye actually sees.

## Part 3 — without photo references

The color path for draft and standard tiers. Substitute written knowledge for
photos, and be strict about the two places unreferenced builds go wrong:

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
   to its lowest 4-neighbour — a 1-voxel skin is a sieve and detaches.
   Restrict slope-shading to real steps (drop ≥ 2) or the whole surface
   flattens.
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
   region (the mount plate), not the whole face.
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

## Last step — harvest anything reusable into `voxel.py`

Fine and ultra tiers only — draft and standard builds end without this pass
(mention that it was skipped so the user can ask for it).

Once the model is finished, read back through the Python you wrote for it and
ask of each helper: **would this be useful on a model of a different subject in
a different style?** A build that needed a helper usually needed it because the
library was missing something, and leaving it in `playground/` means the next
build writes it again, slightly differently.

Promote what passes. Typical of what does:

- a shape or set operation the `shapes.*` family doesn't cover (a wedge, a
  helix, a bevel/chamfer pass over an existing coord set)
- a query or check that is about *voxel models in general* rather than this
  model — support counting under a footprint, surface extraction, a projection
  helper the verification steps kept reimplementing
- a transform, or a palette/color utility with no subject knowledge in it

What stays behind: anything that names this model's parts, encodes its
proportions, or only makes sense for its style. A checker that only ever
applied to one asset is not library tooling (`CLAUDE.md` says this about
`devscripts/` too).

Promoting means the full job, not a paste:

1. Generalize it — drop the caller's assumptions, take a coord set or an axis
   argument where the local version hard-coded one.
2. Match the surrounding API: shape functions return `set[(x,y,z)]`, `Model`
   methods mutate and return `self`, `axis="x"|"y"|"z"` where it applies.
3. Add tests to `test_voxel.py` and run the whole file.
4. Update the API table in `CLAUDE.md` and the tour in `README.md`.
5. Rewrite the build to call the library version, rebuild, and confirm the
   `.vox` is unchanged — that is what proves the generalization was faithful.

If nothing qualifies, say so and move on; a forced promotion costs more than it
saves. Either way, say in the final report what you promoted or why you didn't.

## Done means

At every tier: geometric suite green, and the build script's docstring
carrying the prompt and every follow-up. On top of that, by tier:

- **draft** — the 2D proofread (step 2) done and the step-4 previews read as
  the subject.
- **standard** — the single render round confirms blind identification and
  produced nothing worse than nitpicks.
- **fine / ultra** — a final-round render critique with nothing but nitpicks
  **and** colors within measured tolerance **and** the style table from step 0
  checked off against the finished model — a build that drifted off its agreed
  palette ceiling or detail density is not done, and the drift is only visible
  if the numbers were written down first — **and** the reusable-helper pass
  above run to a decision.
