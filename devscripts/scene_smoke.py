"""Smoke test for Scene: build a real 1024^3 world and read it back.

Checks the whole pipeline at the target scale rather than the toy sizes the
unit tests use -- 64 chunks, structures deliberately laid across chunk seams,
and a per-voxel comparison of the loaded world against what was placed.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxel import Model, Scene, shapes   # noqa: E402

SIZE = 1024
STEP = 128


def build():
    """A Scene spanning 1024^3, with beams crossing every chunk boundary."""
    s = Scene()
    expect = {}

    # A voxel in every one of the 64 chunks, at its far corner.
    for ci in range(4):
        for cj in range(4):
            for ck in range(4):
                p = (ci * 256 + 255, cj * 256 + 255, ck * 256 + 255)
                s.voxel(p, "stone")
                expect[p] = "stone"

    # Beams straddling each interior seam on x, y and z.
    for c in (256, 512, 768):
        for a, b, color in (((c - 6, 0, 0), (c + 5, 0, 0), "red"),
                            ((0, c - 6, 8), (0, c + 5, 8), "green"),
                            ((8, 0, c - 6), (8, 0, c + 5), "blue")):
            for p in shapes.box(a, b):
                s.voxel(p, color)
                expect[p] = color

    # A tower placed from its own Model, to exercise place() and offsets.
    tower = Model()
    tower.cylinder((0, 0, 0), 5, 40, "metal")
    off = (500, 500, 500)
    for p, i in tower.voxels.items():
        expect[(p[0] + off[0], p[1] + off[1], p[2] + off[2])] = "metal"
    s.place(tower, offset=off)
    del tower

    return s, expect


def main():
    s, expect = build()
    path = os.path.join(tempfile.mkdtemp(prefix="scenesmoke"), "world.vox")
    print(s.save(path))
    print(f"scene bounds {s.bounds}  chunks {len(s.chunk_stats())}")

    back = Model.load(path)
    bad = 0
    for p, color in expect.items():
        if p not in back.voxels:
            bad += 1
            if bad < 6:
                print(f"  MISSING {p} ({color})")
        elif back.palette.name(back.voxels[p]) != color:
            bad += 1
            if bad < 6:
                print(f"  WRONG COLOR {p}: want {color}, "
                      f"got {back.palette.name(back.voxels[p])}")
    extra = len(back) - len(expect)
    print(f"loaded {len(back)} voxels, expected {len(expect)}; "
          f"{bad} mismatched, {extra} extra")

    # The beams must not tear at a seam: each is one connected 12-voxel run.
    seams = 0
    for c in (256, 512, 768):
        run = {(x, 0, 0) for x in range(c - 6, c + 6)}
        if run <= set(back.voxels):
            seams += 1
        else:
            print(f"  TORN at x={c}: {sorted(run - set(back.voxels))}")
    print(f"{seams}/3 x-seams intact")

    ok = bad == 0 and extra == 0 and seams == 3
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
