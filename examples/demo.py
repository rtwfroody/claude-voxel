"""Worked examples showing the three ways to build with voxel.py.

    python3 examples/demo.py            # write the .vox files
    python3 examples/demo.py --preview  # ...and show each in the terminal
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from voxel import Model, shapes  # noqa: E402

OUT = os.path.dirname(__file__)


def tree():
    """Idiom 1: primitives plus set algebra.

    add_under lets later foliage blobs pile on without erasing the trunk.
    """
    m = Model()
    m.cylinder((0, 0, 0), 2, 14, "wood")
    for offset, radius in [((0, 0, 20), 8), ((-5, 3, 15), 5), ((6, -2, 16), 5)]:
        m.add_under(shapes.sphere(offset, radius), "leaf")
    # Hollow out a nest: a sphere carved from the canopy, lined with a shell.
    nest = shapes.sphere((6, -2, 16), 3)
    m.remove(nest)
    m.add(shapes.sphere((6, -2, 16), 3, hollow=True) - shapes.box(
        (0, -10, 16), (12, 4, 24)), "dark_brown")
    return m


def robot():
    """Idiom 2: build one half, then mirror.

    Everything is authored at x >= 0 and reflected across x = 0, so the two
    halves cannot drift out of sync. Note that parts meant to cross the
    centerline must start *at* x = 0: column 0 is its own mirror image, so
    starting at x = 1 leaves a one-voxel seam down the middle.
    """
    m = Model()
    m.box((0, -3, 0), (5, 3, 12), "steel")          # torso half
    m.box((0, -2, 13), (4, 2, 19), "metal")         # head half
    m.voxel((3, -3, 17), "cyan")                    # eye
    m.cylinder((7, 0, 11), 1, 9, "steel", axis="z") # shoulder to hand
    m.box((6, -1, 2), (8, 1, 3), "copper")          # hip detail
    m.box((2, -2, -8), (5, 2, -1), "steel")         # leg, gap at centerline
    m.box((2, -3, -10), (5, 3, -9), "dark_grey")    # foot
    m.mirror("x", at=0)
    return m


def house():
    """Idiom 3: ASCII art layers.

    Each string is one Z layer, bottom first, and within a layer the first
    text row is the highest Y -- so each layer reads like a top-down floor
    plan. Use '.' rather than spaces for empty cells: blank leading and
    trailing lines get trimmed, so a row of spaces would shift the geometry.
    """
    def layer(*rows):
        return "\n".join(rows)

    walls_door = layer("wwwwwww", "w.....w", "w.....w", "w.....w",
                       "w.....w", "w.....w", "wwwdwww")
    walls_win = layer("wwwwwww", "w.....w", "w.....w", "g.....g",
                      "w.....w", "w.....w", "wwwdwww")
    walls = layer("wwwwwww", "w.....w", "w.....w", "w.....w",
                  "w.....w", "w.....w", "wwwwwww")
    roof0 = layer(*["rrrrrrr"] * 7)
    roof1 = layer(".......", ".rrrrr.", ".rrrrr.", ".rrrrr.",
                  ".rrrrr.", ".rrrrr.", ".......")
    roof2 = layer(".......", ".......", "..rrr..", "..rrr..",
                  "..rrr..", ".......", ".......")
    roof3 = layer(".......", ".......", ".......", "...r...",
                  ".......", ".......", ".......")

    return Model.from_layers(
        [walls_door, walls_win, walls, walls, roof0, roof1, roof2, roof3],
        {"w": "sand", "d": "dark_brown", "g": "sky", "r": "dark_red"},
    )


BUILDERS = {"tree": tree, "robot": robot, "house": house}

if __name__ == "__main__":
    for name, build in BUILDERS.items():
        m = build()
        print(m.save(os.path.join(OUT, f"{name}.vox")))
        if "--preview" in sys.argv:
            print(m.preview(max_dim=32, ansi="--ansi" in sys.argv))
