"""Compare Model.load before and after the scene-graph change, over a corpus.

Honoring nTRN translations is supposed to change multi-model files and nothing
else. This loads every .vox it is pointed at with both the committed voxel.py
and the working-tree one, and reports which files moved.

    python3 devscripts/load_regression.py <dir-or-file> [...]

Files with no scene graph must come back byte-identical; files with one are
expected to differ, and the difference should be a translation, not a loss.
"""

import glob
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def committed_voxel():
    """Import voxel.py as it is in HEAD, alongside the working-tree one."""
    src = subprocess.run(["git", "-C", ROOT, "show", "HEAD:voxel.py"],
                         capture_output=True, check=True).stdout
    tmp = os.path.join(tempfile.mkdtemp(prefix="voxorig"), "voxel_orig.py")
    with open(tmp, "wb") as f:
        f.write(src)
    return load_module(tmp, "voxel_orig")


def has_graph(path):
    with open(path, "rb") as f:
        return b"nTRN" in f.read()


def result(mod, path):
    try:
        m = mod.Model.load(path)
        return ("ok", len(m), m.bounds)
    except Exception as e:
        return ("error", type(e).__name__, str(e)[:60])


def main(argv):
    files = []
    for arg in argv[1:]:
        files += sorted(glob.glob(os.path.join(arg, "*.vox"))) \
            if os.path.isdir(arg) else [arg]
    if not files:
        print(__doc__)
        return 2

    old = committed_voxel()
    new = load_module(os.path.join(ROOT, "voxel.py"), "voxel_new")

    same = moved = split = broke = 0
    for path in files:
        a, b = result(old, path), result(new, path)
        graph = has_graph(path)
        name = os.path.basename(path)
        if a == b:
            same += 1
            if graph and a[0] == "ok" and a[1]:
                print(f"  note   {name}: has a scene graph but did not move")
        elif not graph:
            broke += 1
            print(f"  BROKE  {name}: {a} -> {b}   (no scene graph, must match)")
        elif b[0] == "error":
            broke += 1
            print(f"  BROKE  {name}: {a} -> {b}")
        elif a[1] > b[1]:
            broke += 1
            print(f"  BROKE  {name}: lost voxels, {a[1]} -> {b[1]}")
        elif a[1] < b[1]:
            # Expected: the old loader stacked every model at the origin, so
            # models that overlap there collapsed onto each other. Pulling
            # them apart uncovers the voxels that used to be overwritten.
            split += 1
            print(f"  split  {name}: {a[1]} -> {b[1]} voxels "
                  f"(models no longer collide)")
        else:
            moved += 1
            print(f"  moved  {name}: {a[2]} -> {b[2]}")

    print(f"\n{len(files)} files: {same} unchanged, {moved} translated, "
          f"{split} un-collided, {broke} broken")
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
