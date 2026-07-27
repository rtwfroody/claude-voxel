"""Inspect the scene graph of a .vox file written by something other than us.

Our own Scene.save -> Model.load round trip cannot prove the `_t`-is-the-center
convention, because load subtracts exactly what save added. This dumps the raw
graph so it can be compared against a reference implementation.

The convention was settled against ogt_vox.h (the reader vengi vendors), which
states that the pivot of a model is at floor(size / 2), and against vengi's
writer, which emits `_t = lower_corner + size / 2`. Both agree: `_t` is the
center, and the corner is `_t - size // 2` with *floor* division.

    python3 devscripts/check_real_vox.py <file.vox> [...]

Caveat this script exists to make visible: real MagicaVoxel files lean on `_r`
rotations, which Model.load ignores. Identity is `_r` absent or "4" -- not 0.
A foreign file whose transforms rotate will load with those models misplaced.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxel import Model, _read_dict, _walk_chunks   # noqa: E402


def dump(path):
    data = open(path, "rb").read()
    sizes, counts, trns, shps = [], [], [], []
    for cid, content in _walk_chunks(data, 8):
        if cid == b"SIZE":
            sizes.append(struct.unpack_from("<iii", content, 0))
        elif cid == b"XYZI":
            counts.append(struct.unpack_from("<i", content, 0)[0])
        elif cid == b"nTRN":
            node = struct.unpack_from("<i", content, 0)[0]
            _, off = _read_dict(content, 4)
            child, _reserved, layer = struct.unpack_from("<iii", content, off)
            frames = struct.unpack_from("<i", content, off + 12)[0]
            frame, _ = _read_dict(content, off + 16)
            trns.append((node, child, layer, frames, frame.get("_t"),
                         frame.get("_r")))
        elif cid == b"nSHP":
            node = struct.unpack_from("<i", content, 0)[0]
            _, off = _read_dict(content, 4)
            shps.append((node, struct.unpack_from("<i", content, off + 4)[0]))

    print(f"\n=== {os.path.basename(path)}")
    more = "..." if len(sizes) > 6 else ""
    print(f"models {len(sizes)}  sizes {sizes[:6]}{more}")
    print(f"voxels per model {counts[:6]}{more}")
    for node, child, layer, frames, t, r in trns[:8]:
        print(f"  nTRN node={node} child={child} layer={layer} "
              f"frames={frames} _t={t!r} _r={r!r}")
    for node, mid in shps[:8]:
        print(f"  nSHP node={node} model={mid}")

    rotated = [t for t in trns if t[5] not in (None, "4")]
    if rotated:
        print(f"  {len(rotated)}/{len(trns)} transforms are rotated -- "
              "Model.load will misplace those models")
    animated = [t for t in trns if t[3] > 1]
    if animated:
        print(f"  {len(animated)} transforms are animated; we read frame 0")

    m = Model.load(path)
    print(f"loaded: {m.stats()}")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    for path in argv[1:]:
        dump(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
