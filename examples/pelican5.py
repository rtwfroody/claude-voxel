"""A brown pelican in breeding plumage, soaring.

The point of this model is the *pose*.  A pelican in a glide holds its neck
retracted in a tight S so the head rests back on the shoulders and only the
long bill projects forward; a pelican built with its neck stretched out reads
as a stork.  Everything else here serves that silhouette.

Geometry sourcing (see scratchpad refs5/ratios.md):

  * The wing chord taper is MEASURED -- median over 13 wing samples from vetted
    spread-wing photographs, normalised to half-span so that body
    foreshortening in the reference frames cannot contaminate it.
  * Overall length ratios are from literature (total length 1.0-1.37 m,
    wingspan 2.0-2.3 m, bill 0.28-0.35 m).  Photographic measurement of these
    was attempted and failed: PCA cannot tell a wingspan from a body axis, and
    span/L came out anywhere from 1.56 to 8.06 across frames of one species.
  * The wrist at half the half-span is anatomical knowledge, not measured --
    automated detection returned noise (0.05-0.95).

Coordinates are right-handed with Z up: +X right, +Y away from the viewer,
+Z up.  The bird faces -Y, so the bill tip is at minimum Y.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxel import Model, shapes  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "pelican5.vox")
SEED = 20260729

# ---------------------------------------------------------------- palette
# Measured off vetted in-flight photographs; see scratchpad refs5/palette.md for
# the patch table and refs5/swatches.png for the montage each value was read
# from.  Principle: MEASURE, THEN STYLISE -- measured hue and the measured
# ordering of tones are kept; what is stretched is the *spacing*, because
# photo-accurate medians read as mud at voxel scale.
#
# The measurement overturned the received wisdom in notes.md ("a brown pelican
# is grey", mantle #57575a).  That note is right about HUE -- the pale tracts
# really are near-neutral, chroma 7-8 -- but wrong about VALUE: they are
# genuinely LIGHT (foreneck #dcdad5, mantle #c5c1bd), not mid-grey.  The real
# bird spans luma ~218 to ~16, about 13:1.  Compressing that at the pale end is
# what made the previous build read as a flat grey blob.
#
# A second trap found on the way: sampling inside a silhouette mask returns only
# the DARK parts of a pelican, because a white head is bright and near-neutral
# and merges with the sky.  Every value here excludes sky by blue-dominance
# instead, which keeps white plumage.
CORE            = "#40352c"   # interior, so a cut-away reads as a body
MANTLE          = "#8e8983"   # measured #c5c1bd chroma 8; darkened so the
                              # wing covert panel stays the brightest thing
SCAPULAR        = "#7a756f"
RUMP            = "#5c5852"
COVERT          = "#a09b92"   # the pale upperwing panel -- the readable feature
COVERT_EDGE     = "#b8b3aa"
GREATER         = "#4a443c"
TERTIAL         = "#8c877e"
SECONDARY       = "#2a2621"
PRIMARY         = "#1b1815"   # measured #1b1815 chroma 6, 98% non-sky
PRIMARY_COVERT  = "#46403a"
UNDER_COVERT    = "#b4b0a8"   # pale central underwing band
UNDER_REMIGE    = "#24211d"
BREAST          = "#45403a"
BELLY           = "#1a1815"   # measured #110f0f chroma 2
FLANK           = "#2a2620"
UNDERTAIL       = "#221f1b"
TAIL_DARK       = "#2e2a25"   # measured #181715, lifted: at #22201c it was
                              # indistinguishable from the rump and read as no tail
TAIL_EDGE       = "#5a544c"   # pale feather tips, so the tail has an outline
HEAD_WHITE      = "#e8e6e0"   # measured foreneck #dcdad5 chroma 7
CROWN_YELLOW    = "#d9c69a"   # measured #d1c2af chroma 34, pushed warmer
HINDNECK        = "#5e3826"   # narrow nape stripe ONLY -- see NAPE_FLANK
FORENECK        = "#d3d1cc"   # measured #dcdad5 -- the neck is WHITE in breeding
FACE_SKIN       = "#2b2a33"   # not measured (too small in frame); knowledge
EYE             = "#e6ebf0"
BILL_PALE       = "#c2a89c"   # measured #c2a49c chroma 38 -- pinkish grey
BILL_RIDGE      = "#a68b80"
BILL_NAIL       = "#92401b"
# Pouch is a GRADIENT, measured: dark maroon on the upper pouch, bright rust
# along the lower/leading edge.  It is emphatically not uniformly red.
POUCH_A         = "#7d3c2b"   # measured #86462f chroma 87, pulled back: see
                              # the note on shallow sections in build()
POUCH_B         = "#6a3228"
POUCH_C         = "#5c2f27"   # measured #572e28 chroma 47
POUCH_D         = "#4a2724"   # throat end, darkest
FOOT            = "#17161a"

# ---------------------------------------------------------------- scale
L = 116                  # total length, bill tip to tail tip
HALF_SPAN = 105          # so total width is 211 -- ODD, see the mirror note

# ---------------------------------------------------------------- body
BILL_TIP_Y = -54
BILL_BASE_Y = -21        # 33 long = 0.27 L, within the 0.25-0.28 range
TORSO_Y0, TORSO_Y1 = -6, 52
TAIL_Y0, TAIL_Y1 = 52, 66

# (y, half_width, z_centre, half_height) -- lofted elliptical sections.
# The four forward sections exist to sweep the breast up to the throat.  Without
# them the torso began at y=-6 with a 10-voxel vertical wall under the neck --
# the "sheer face" class from notes.md, which no connectivity check can see.
BODY = [
    (-14, 3.2,  8.0,  3.0),
    (-11, 4.0,  6.5,  4.5),
    (-8,  5.0,  4.5,  6.0),
    (-6,  6.0,  3.0,  7.5),
    (0,   9.0,  1.0, 10.0),
    (8,  11.0,  0.0, 12.0),
    (18, 11.0,  0.0, 12.0),
    (28,  9.5,  0.5, 11.0),
    (38,  7.0,  1.0,  9.0),
    (46,  5.0,  2.0,  6.5),
    (52,  3.5,  3.0,  4.5),
]
KEEL = 0.30              # how much the section narrows below its centreline

HEAD_C = (0, -16, 16)    # head sits ON the shoulders, aft of the bill base
HEAD_R = (5.6, 9.2, 5.6)
NECK = [(2, 8, 6.5), (-4, 11, 6.0), (-9, 13.5, 5.5), (-13, 15, 5.0)]

BILL_Z = 12.0
BILL_HW = [(-54, 1.1), (-46, 1.7), (-38, 2.4), (-29, 3.0), (-21, 3.4)]
BILL_HH = [(-54, 1.1), (-46, 1.5), (-38, 2.0), (-29, 2.4), (-21, 2.7)]
BILL_NAIL_FRAC = 0.10    # last tenth of the bill is the orange nail

POUCH_Y0, POUCH_Y1 = -48, -14
# Depth below the bill, shallow at the distal end and deepest at the throat.
# Getting this backwards both inverts the anatomy and puts a 9-voxel cliff at
# the pouch's leading edge.
POUCH_FLOOR = [(-48, 1.4), (-40, 2.8), (-33, 4.6), (-26, 6.6),
               (-20, 8.2), (-14, 8.8)]
# Kept narrower than the head's half-width (5.6) so the pouch reads as slung
# under the bill rather than as a second head.
POUCH_HW = [(-48, 1.4), (-40, 2.6), (-33, 3.8), (-26, 4.6),
            (-20, 4.9), (-14, 4.6)]

TAIL_HW = [(52, 8.0), (58, 7.0), (62, 5.6), (66, 3.4)]
TAIL_Z = [(52, 3.0), (58, 2.6), (63, 2.2), (66, 2.0)]
TAIL_HH = [(52, 2.6), (58, 2.0), (62, 1.4), (66, 1.0)]   # thins toward the tip

FOOT_Y0, FOOT_Y1 = 46, 60

# ---------------------------------------------------------------- wing
WING_X0 = 8              # embedded in the flank, so the wing cannot detach
WING_ROOT_F = 0.20       # inboard of this the reference measured body, not wing

# MEASURED: chord as a fraction of half-span, by span fraction |x|/HALF_SPAN
CHORD = [(0.20, 0.279), (0.30, 0.247), (0.40, 0.235), (0.50, 0.232),
         (0.60, 0.201), (0.70, 0.186), (0.80, 0.164), (0.90, 0.141),
         (1.00, 0.010)]
# Leading edge, aft-positive.  MEASURED from the reference masks: sweep rises
# quickly over the arm to the wrist and then PLATEAUS, so the hand runs almost
# straight out.  Total root-to-tip sweep is only ~10 voxels at this half-span.
#
# Round 0 had 29 voxels here and rendered as a swept, swift-like X.  The error
# was mine and worth recording: the reference "lead" table is measured relative
# to the body's most forward point -- the BILL TIP -- while the model's is
# relative to the wing root.  Comparing the two directly made 28 voxels of
# sweep look confirmed when the root-relative figure is a third of that.
LEAD = [(0.20, -2.0), (0.30, 3.0), (0.40, 6.0), (0.50, 8.5),
        (0.60, 10.0), (0.70, 11.0), (0.85, 10.5), (1.00, 9.0)]
# a shallow arch: soaring brown pelicans glide on nearly flat wings
ARCH = [(0.20, 6.0), (0.40, 8.5), (0.60, 10.0), (0.80, 9.5), (1.00, 8.0)]
THICK = [(0.20, 5.2), (0.30, 3.8), (0.45, 2.6), (0.60, 1.9),
         (0.80, 1.1), (1.00, 0.5)]

FINGER_F = 0.78          # span fraction where the primaries separate
FINGER_COUNT = 5
FINGER_GAP = 0.55        # fraction of chord removed in a slot

# region boundaries, as fractions -- one number each so they can be retuned
COVERT_CHORD_END = 0.38  # coverts occupy the leading part of the arm
TERTIAL_F_END = 0.26
ARM_F_END = 0.55         # outboard of this is the hand (primaries)
SEAM_TILT = 0.13         # tilts the arm/hand seam so it is not radial
PCOV_CHORD_END = 0.32
UCOV_CHORD_END = 0.52
NAPE_HW = 4              # chestnut stripe half-width CAP -- MUST stay narrow
NAPE_FLANK = 3           # voxels of head colour left outboard on each side
NAPE_Y0, NAPE_Y1 = -14, -2
MANTLE_Z = 4.0


def interp(table, t):
    """Piecewise-linear lookup on a sorted (key, value) table."""
    if t <= table[0][0]:
        return table[0][1]
    if t >= table[-1][0]:
        return table[-1][1]
    for (a, va), (b, vb) in zip(table, table[1:]):
        if a <= t <= b:
            return va + (vb - va) * (t - a) / (b - a)
    return table[-1][1]


def body_coords():
    out = set()
    y0, y1 = BODY[0][0], BODY[-1][0]
    for y in range(y0, y1 + 1):
        hw = interp([(a, b) for a, b, _, _ in BODY], y)
        zc = interp([(a, c) for a, _, c, _ in BODY], y)
        hh = interp([(a, d) for a, _, _, d in BODY], y)
        for z in range(int(math.floor(zc - hh)), int(math.ceil(zc + hh)) + 1):
            t = (z - zc) / hh
            if abs(t) > 1.0:
                continue
            w = hw * math.sqrt(max(0.0, 1.0 - t * t))
            if t < 0:
                w *= 1.0 - KEEL * (-t)      # keel: narrower below the centreline
            iw = int(round(w))
            for x in range(-iw, iw + 1):
                out.add((x, y, z))
    return out


def neck_coords():
    """The retracted S: a short thick column from the breast up to the head."""
    out = set()
    for (y0, z0, r0), (y1, z1, r1) in zip(NECK, NECK[1:]):
        steps = max(abs(y1 - y0), abs(z1 - z0)) * 3 + 1
        for i in range(steps + 1):
            t = i / steps
            y = y0 + (y1 - y0) * t
            z = z0 + (z1 - z0) * t
            r = r0 + (r1 - r0) * t
            out |= shapes.sphere((0, int(round(y)), int(round(z))), r)
    return out


def head_coords():
    return shapes.ellipsoid(HEAD_C, HEAD_R)


def bill_coords():
    out = set()
    for y in range(BILL_TIP_Y, BILL_BASE_Y + 1):
        hw = interp(BILL_HW, y)
        hh = interp(BILL_HH, y)
        for z in range(int(math.floor(BILL_Z - hh)), int(math.ceil(BILL_Z + hh)) + 1):
            t = (z - BILL_Z) / hh
            if abs(t) > 1.0:
                continue
            w = hw * math.sqrt(max(0.0, 1.0 - t * t * 0.55))   # slab-sided
            iw = int(round(w))
            for x in range(-iw, iw + 1):
                out.add((x, y, z))
    return out


def pouch_coords():
    """Slack gular pouch slung under the bill and throat."""
    out = set()
    for y in range(POUCH_Y0, POUCH_Y1 + 1):
        depth = interp(POUCH_FLOOR, y)
        hw = interp(POUCH_HW, y)
        top = BILL_Z - 1
        bot = BILL_Z - depth
        for z in range(int(math.floor(bot)), int(math.ceil(top)) + 1):
            span = top - bot
            t = (z - bot) / span if span > 0 else 1.0
            # Widest in the middle and rounded at the bottom, so the pouch is a
            # SACK.  The previous profile tapered monotonically to the floor and
            # rendered as a thin blade -- a blind viewer called it "a fin, not a
            # bag", losing the one feature that most says pelican.
            w = hw * math.sqrt(max(0.0, 1.0 - (2.0 * t - 1.0) ** 2 * 0.55))
            iw = max(1, int(round(w)))
            for x in range(-iw, iw + 1):
                out.add((x, y, z))
    return out


def tail_coords():
    """Short closed tail.  Rounded in section and tapered in both width and
    thickness -- built as a plain box it renders as a cardboard carton stuck to
    the rump."""
    out = set()
    for y in range(TAIL_Y0, TAIL_Y1 + 1):
        hw = interp(TAIL_HW, y)
        zc = interp(TAIL_Z, y)
        hh = interp(TAIL_HH, y)
        for z in range(int(math.floor(zc - hh)), int(math.ceil(zc + hh)) + 1):
            t = (z - zc) / hh
            if abs(t) > 1.0:
                continue
            w = hw * math.sqrt(max(0.0, 1.0 - t * t * 0.55))
            iw = int(round(w))
            for x in range(-iw, iw + 1):
                out.add((x, y, z))
    return out


def foot_coords():
    """Webbed feet tucked back under the base of the tail."""
    out = set()
    for side in (-1, 1):
        for y in range(FOOT_Y0, FOOT_Y1 + 1):
            t = (y - FOOT_Y0) / (FOOT_Y1 - FOOT_Y0)
            zc = 1.0 - 5.0 * t
            hw = 1.6 + 1.8 * t
            xc = side * 3
            for z in (int(round(zc)), int(round(zc)) + 1):
                iw = int(round(hw))
                for dx in range(-iw, iw + 1):
                    out.add((xc + dx, y, z))
    return out


def wing_geometry():
    """Loft each wing over x.  Returns {coord: (f, chord_frac, above)}."""
    info = {}
    for x in range(-HALF_SPAN, HALF_SPAN + 1):
        ax = abs(x)
        if ax < WING_X0:
            continue
        f = ax / HALF_SPAN
        fq = max(f, WING_ROOT_F)          # inboard of 0.20 hold the root value
        chord = interp(CHORD, fq) * HALF_SPAN
        le = interp(LEAD, fq)
        zc = interp(ARCH, fq)
        th = interp(THICK, fq)
        if chord < 1.0:
            continue
        y0 = int(round(le))
        y1 = int(round(le + chord))
        for y in range(y0, y1 + 1):
            c = (y - le) / chord
            # Tolerate a hair outside [0,1] and clamp, rather than dropping the
            # voxel.  Rounding the leading edge to a voxel column puts it at a
            # slightly negative chord fraction, and discarding it opened a
            # one-voxel gap that detached the wingtip.
            if c < -0.06 or c > 1.06:
                continue
            c = min(max(c, 0.0), 1.0)
            if fingered_out(f, c, x):
                continue
            # thickest around 35% chord, thinning to both edges
            tl = th * math.sqrt(max(0.0, 1.0 - ((c - 0.35) / 0.68) ** 2))
            tl = max(tl, 0.5)
            zlo = int(round(zc - tl))
            zhi = int(round(zc + tl))
            if zhi < zlo:
                zhi = zlo
            for z in range(zlo, zhi + 1):
                info[(x, y, z)] = (f, c, z >= zc)
    return info


def fingered_out(f, c, x):
    """True where a primary slot removes the trailing part of the chord."""
    if f < FINGER_F:
        return False
    span = 1.0 - FINGER_F
    k = (abs(x) / HALF_SPAN - FINGER_F) / span      # 0..1 across the hand
    phase = k * FINGER_COUNT
    frac = phase - math.floor(phase)
    # a narrow slot at each feather boundary, trailing portion only
    return frac < 0.22 and c > (1.0 - FINGER_GAP)


def build():
    m = Model()

    body = body_coords()
    neck = neck_coords()
    head = head_coords()
    bill = bill_coords()
    # The pouch is painted after the head, so any overlap would repaint the
    # head's lower flanks rust -- round 0 read as an orange mass wrapping the
    # face.  A pelican's pouch hangs BELOW the bill and throat; it is never
    # beside the head.  Subtracting the head enforces that.
    pouch = pouch_coords() - head
    tail = tail_coords()
    feet = foot_coords()
    wing = wing_geometry()

    # ---- base coats, coarse to fine (later add wins) -------------------
    m.add(body | neck | tail, CORE)

    # body: upper surface is mantle, lower is breast/belly, aft is rump
    # The dorsal surface darkens from the pale mantle at the shoulders back to
    # the rump.  Round 0 painted the whole back one pale tone, which read as a
    # single flat mass -- the exact complaint this build exists to fix, just
    # displaced from the wings onto the body.
    for (x, y, z) in body | neck:
        if z >= MANTLE_Z:
            if y >= 40:
                c = RUMP
            elif y >= 24:
                c = SCAPULAR
            else:
                c = MANTLE if abs(x) <= 7 else SCAPULAR
        elif y >= 40:
            c = UNDERTAIL
        elif z <= -3:
            c = BELLY if y >= 6 else BREAST
        else:
            c = FLANK if y >= 6 else BREAST
        m.voxel((x, y, z), c)

    # foreneck: the front of the retracted neck, above the breast
    for (x, y, z) in neck:
        if z >= 6 and y <= 2:
            m.voxel((x, y, z), FORENECK)

    m.add(tail, TAIL_DARK)
    # Pale feather tips: the tail's own colour alone left it invisible against
    # the rump.  Restricted to the distal third and the outer edge so it reads as
    # an outline rather than a stripe.
    t_lo = TAIL_Y0 + (TAIL_Y1 - TAIL_Y0) * 2 // 3
    edge = {(x, y, z) for (x, y, z) in tail
            if y >= t_lo or abs(x) >= interp(TAIL_HW, y) - 1.0}
    m.add(edge, TAIL_EDGE)

    # ---- wings ---------------------------------------------------------
    for (p, (f, c, above)) in wing.items():
        # Tilt the arm/hand boundary with chord so the seam runs diagonally
        # across the feather tracts instead of radially.
        hand = f >= ARM_F_END - SEAM_TILT * (1.0 - c)
        if above:
            if hand:
                col = PRIMARY_COVERT if c <= PCOV_CHORD_END else PRIMARY
            elif f <= TERTIAL_F_END and c > COVERT_CHORD_END:
                col = TERTIAL
            elif c <= COVERT_CHORD_END:
                col = COVERT
            else:
                col = GREATER if c <= 0.72 else SECONDARY
        else:
            if not hand and c <= UCOV_CHORD_END:
                col = UNDER_COVERT
            else:
                col = UNDER_REMIGE
        m.voxel(p, col)

    # a paler outer edge on the covert panel, to lift it off the dark remiges
    for (p, (f, c, above)) in wing.items():
        if above and f < ARM_F_END and c <= 0.16:
            m.voxel(p, COVERT_EDGE)

    # ---- head, bill, pouch --------------------------------------------
    m.add(head, HEAD_WHITE)

    # crown: the upper half of the head carries the breeding yellow wash
    for (x, y, z) in head:
        if z >= HEAD_C[2] + 1:
            m.voxel((x, y, z), CROWN_YELLOW)

    # chestnut nape stripe -- CLAMPED IN X.  Painting this without an x-clamp
    # is what produced the "brown hump" saddle across the whole back before.
    # Width follows the neck's LOCAL width so a flank of head colour always
    # survives on both sides.  A fixed half-width left only 1 voxel of flank
    # where the neck narrows, which starts to read as a wrap.
    nape = set()
    for y in range(NAPE_Y0, NAPE_Y1 + 1):
        ring = [(x, yy, z) for (x, yy, z) in head | neck if yy == y and z >= 8]
        if not ring:
            continue
        local = max(abs(x) for x, _, _ in ring)
        w = min(NAPE_HW, local - NAPE_FLANK)
        if w < 1:
            continue
        nape |= {(x, yy, z) for (x, yy, z) in ring if abs(x) <= w}
    m.add(nape, HINDNECK)

    # bare facial skin and the eye
    for (x, y, z) in head:
        if abs(x) >= HEAD_R[0] - 1.6 and -21 <= y <= -17 and 16 <= z <= 17:
            m.voxel((x, y, z), FACE_SKIN)
    for side in (-1, 1):
        m.voxel((side * (int(HEAD_R[0]) - 1), -19, 17), EYE)

    m.add(bill, BILL_PALE)
    for (x, y, z) in bill:
        if abs(x) <= 1 and z >= BILL_Z:
            m.voxel((x, y, z), BILL_RIDGE)
    nail_y = BILL_TIP_Y + (BILL_BASE_Y - BILL_TIP_Y) * BILL_NAIL_FRAC
    for (x, y, z) in bill:
        if y <= nail_y:
            m.voxel((x, y, z), BILL_NAIL)

    # The pouch is not a flat slab and not uniformly red.  Measured: dark maroon
    # over the upper pouch, with a bright rust band along the LOWER edge (upper
    # #572e28 chroma 47 vs lower #86462f chroma 87).  So the gradient runs
    # ventrally, not along the length, with a mild along-length darkening toward
    # the throat.  Shading the underside is safe; shading the lateral rim is what
    # notes.md forbids, because the rim is what every side projection shows.
    for (x, y, z) in pouch:
        m.voxel((x, y, z), POUCH_C if y <= -29 else POUCH_D)
    floor_of = {}
    for (x, y, z) in pouch:
        floor_of[(x, y)] = min(floor_of.get((x, y), 99), z)
    for (x, y, z) in pouch:
        d = z - floor_of[(x, y)]
        if d == 0:
            m.voxel((x, y, z), POUCH_A)
        elif d <= 2:
            m.voxel((x, y, z), POUCH_B)

    m.add(feet, FOOT)

    # Interior last: anything fully enclosed is never visible from outside, so
    # give it its own colour and a cut-away reads as a body rather than a blob.
    solid = m.coords()
    interior = {(x, y, z) for (x, y, z) in body | neck | tail
                if {(x + 1, y, z), (x - 1, y, z), (x, y + 1, z),
                    (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)} <= solid}
    m.add(interior, CORE)
    return m


def check(m):
    """Geometric and pose verification.  Prints pass/fail lines."""
    ok = True
    lo, hi = m.bounds
    size = m.size
    print(f"voxels {len(m)}  size {size}  bounds {lo}..{hi}")

    # width must be odd so the mirror plane lands on a voxel column
    odd = size[0] % 2 == 1
    print(f"[{'ok' if odd else 'FAIL'}] width {size[0]} is odd")
    ok &= odd

    # exact mirror symmetry about x=0
    cs = m.coords()
    asym = {p for p in cs if (-p[0], p[1], p[2]) not in cs}
    print(f"[{'ok' if not asym else 'FAIL'}] mirror symmetry: {len(asym)} unpaired")
    ok &= not asym

    det = m.detached()
    print(f"[{'ok' if not det else 'FAIL'}] detached voxels: {len(det)}")
    ok &= not det

    # unused-palette check
    used = {m.palette.rgba(i) for i in set(m.voxels.values())}
    from voxel import parse_color
    defined = {
        "CORE": CORE, "MANTLE": MANTLE, "SCAPULAR": SCAPULAR, "RUMP": RUMP,
        "COVERT": COVERT, "COVERT_EDGE": COVERT_EDGE, "GREATER": GREATER,
        "TERTIAL": TERTIAL, "SECONDARY": SECONDARY, "PRIMARY": PRIMARY,
        "PRIMARY_COVERT": PRIMARY_COVERT, "UNDER_COVERT": UNDER_COVERT,
        "UNDER_REMIGE": UNDER_REMIGE, "BREAST": BREAST, "BELLY": BELLY,
        "FLANK": FLANK, "UNDERTAIL": UNDERTAIL, "TAIL_DARK": TAIL_DARK,
        "TAIL_EDGE": TAIL_EDGE,
        "HEAD_WHITE": HEAD_WHITE, "CROWN_YELLOW": CROWN_YELLOW,
        "HINDNECK": HINDNECK, "FORENECK": FORENECK, "FACE_SKIN": FACE_SKIN,
        "EYE": EYE, "BILL_PALE": BILL_PALE, "BILL_RIDGE": BILL_RIDGE,
        "BILL_NAIL": BILL_NAIL, "POUCH_A": POUCH_A, "POUCH_B": POUCH_B,
        "POUCH_C": POUCH_C, "POUCH_D": POUCH_D, "FOOT": FOOT,
    }
    missing = [n for n, c in defined.items() if parse_color(c) not in used]
    print(f"[{'ok' if not missing else 'FAIL'}] unused palette: {missing}")
    ok &= not missing

    # ---- pose assertions ----------------------------------------------
    head_front = HEAD_C[1] - HEAD_R[1]
    a = BILL_BASE_Y < interp(LEAD, WING_ROOT_F) + TORSO_Y0 + 6
    print(f"[{'ok' if a else 'FAIL'}] bill base ({BILL_BASE_Y}) forward of "
          f"wing root LE ({interp(LEAD, WING_ROOT_F):.0f})")
    ok &= a
    # The pelican4 postmortem: its side mask put most of the bill's length
    # *under* the head, so only a ~5-voxel stub protruded and the render read as
    # a dodo.  The head overlapping the bill BASE is correct anatomy -- the bill
    # emerges from the face -- so the meaningful test is how far the bill sticks
    # out past the head, measured against head length.
    protrude = head_front - BILL_TIP_Y
    head_len = 2 * HEAD_R[1]
    b = protrude >= 0.60 * head_len
    print(f"[{'ok' if b else 'FAIL'}] bill protrudes {protrude:.0f} beyond the "
          f"head front, {protrude / head_len:.2f}x head length (want >=0.60)")
    ok &= b
    c = HEAD_C[1] > BILL_BASE_Y
    print(f"[{'ok' if c else 'FAIL'}] head centre ({HEAD_C[1]}) aft of bill base")
    ok &= c

    # the chestnut must be a narrow nape stripe, never a saddle on the back
    from voxel import parse_color as pc
    chest = {p for p, i in m.voxels.items() if m.palette.rgba(i) == pc(HINDNECK)}
    torso_hw = max(b for _, b, _, _ in BODY)

    # Compare the stripe against the LOCAL BODY WIDTH at the same y, not against
    # NAPE_HW.  A check keyed on the constant the paint is derived from cannot
    # fail -- notes.md logs four separate instances.  This version fails if the
    # x-clamp is ever dropped, which is the bug that actually happened
    # (chestnut wrapping the whole width as a saddle: the "brown hump").
    # A stripe is FLANKED: at every y it occupies, un-chestnut voxels must remain
    # outboard of it on both sides.  That is structural and holds however thin
    # the neck gets, unlike a coverage fraction -- the neck is only ~11 wide, so
    # a legitimate 9-wide stripe covers 82% of it and any percentage threshold
    # sits uselessly close to the failure case.
    worst_y, margin = None, 99
    for y in sorted({y for _, y, _ in chest}):
        cx = max(abs(x) for x, yy, _ in chest if yy == y)
        bx = max(abs(x) for x, yy, _ in m.coords() if yy == y)
        if bx - cx < margin:
            worst_y, margin = y, bx - cx
    d = margin >= 2
    print(f"[{'ok' if d else 'FAIL'}] chestnut is a flanked stripe, not a wrap: "
          f"thinnest flank {margin} voxels at y={worst_y} (want >=2)")
    ok &= d

    # and it must stay on the REAR surface of the neck, not creep round the front
    fore = [p for p in chest if p[1] < HEAD_C[1] - HEAD_R[1]]
    e = not fore
    print(f"[{'ok' if e else 'FAIL'}] chestnut stays behind the face: "
          f"{len(fore)} voxels forward of y={HEAD_C[1] - HEAD_R[1]:.0f}")
    ok &= e

    # Primary fingering: the slots are notches in the TRAILING edge, not holes
    # in the middle of the wing, so count local minima along the hand.  An
    # interior-gap test reported 0 of 98 stations and looked like a total
    # failure when the feature was in fact present.
    te = {}
    for (x, y, z) in m.coords():
        if x > 0:
            te[x] = max(te.get(x, -999), y)
    hand = [te[x] for x in sorted(te) if x / HALF_SPAN >= FINGER_F]
    notches = sum(1 for i in range(1, len(hand) - 1)
                  if hand[i] < hand[i - 1] and hand[i] <= hand[i + 1])
    g = notches >= FINGER_COUNT - 1
    print(f"[{'ok' if g else 'FAIL'}] primaries show {notches} finger notches "
          f"(want >={FINGER_COUNT - 1})")
    ok &= g

    # ---- ratios, measured off the model itself ------------------------
    span = m.size[0]
    total = m.size[1]
    print(f"\n{'quantity':<26}{'model':>9}{'target':>9}")
    rows = [
        ("wingspan / L", span / total, 1.75),
        ("bill / L", (BILL_BASE_Y - BILL_TIP_Y) / total, 0.25),
        ("head / L", (2 * HEAD_R[1]) / total, 0.15),
        ("torso / L", (TORSO_Y1 - TORSO_Y0) / total, 0.50),
        ("tail / L", (TAIL_Y1 - TAIL_Y0) / total, 0.12),
        ("torso width / L", (2 * torso_hw) / total, 0.19),
    ]
    for name, got, want in rows:
        flag = "" if abs(got - want) < 0.04 else "  <-- drift"
        print(f"{name:<26}{got:>9.3f}{want:>9.3f}{flag}")

    print(f"\n{'span f':<8}{'chord model':>12}{'chord measured':>16}")
    for f, frac in CHORD:
        x = int(round(f * HALF_SPAN))
        ys = [y for (xx, y, _) in m.coords() if xx == x]
        got = (max(ys) - min(ys) + 1) if ys else 0
        print(f"{f:<8.2f}{got:>12}{frac * HALF_SPAN:>16.1f}")

    # ---- underside profile: look for sheer walls ----------------------
    floor = {}
    for (x, y, z) in m.coords():
        if abs(x) <= 6:
            floor[y] = min(floor.get(y, 99), z)
    ys = sorted(floor)
    steps = [(y, floor[y] - floor[y - 1]) for y in ys[1:]
             if abs(floor[y] - floor[y - 1]) >= 4 and not (FOOT_Y0 - 2 <= y <= FOOT_Y1 + 2)]
    print(f"\n[{'ok' if not steps else 'WARN'}] underside steps >=4 outside the "
          f"feet: {steps}")
    return ok


if __name__ == "__main__":
    m = build()
    m.save(OUT)
    print(f"wrote {OUT}")
    good = check(m)
    if "-p" in sys.argv or "--preview" in sys.argv:
        print(m.preview(max_dim=100, views=("front", "side", "top")))
    sys.exit(0 if good else 1)
