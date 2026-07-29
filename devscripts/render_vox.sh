#!/bin/sh
# Render a .vox to PNGs using vengi's headless thumbnailer.
#
#   devscripts/render_vox.sh examples/pelican5.vox /tmp/out 512
#
# Writes <outdir>/<name>_NN.png -- a 16-step turntable -- plus <name>_iso.png.
#
# Needs `vengi-thumbnailer` on PATH; set THUMB=/path/to/it to override.
#
# Caveats found the hard way (see notes.md):
#   * -a/--angles, -d/--distance and --sunelevation are ACCEPTED AND IGNORED.
#     Passing different values gives byte-identical PNGs.  The only flags that
#     actually move the camera are -t (turntable) and --image --isometric.
#     Always md5sum the output before believing you have several viewpoints.
#   * The thumbnailer is an ASan build and prints a leak report on exit.  That
#     is noise, not failure -- check that the PNG is non-empty instead.
#   * Turntable ignores -o's basename directory only if it does not exist, so
#     the output directory is created first.
set -eu

THUMB=${THUMB:-vengi-thumbnailer}
command -v "$THUMB" >/dev/null 2>&1 || {
    echo "$0: '$THUMB' not found on PATH; set THUMB=/path/to/vengi-thumbnailer" >&2
    exit 1
}

IN=$(readlink -f "$1")
OUT=${2:-/tmp/voxrender}
SIZE=${3:-512}

mkdir -p "$OUT"
NAME=$(basename "$IN" .vox)

"$THUMB" -i "$IN" -o "$OUT/$NAME.png" -s "$SIZE" -t   >/dev/null 2>&1 || true
"$THUMB" -i "$IN" -o "$OUT/${NAME}_iso.png" -s "$SIZE" \
    --image --isometric                               >/dev/null 2>&1 || true

n=$(md5sum "$OUT"/${NAME}*.png | awk '{print $1}' | sort -u | wc -l)
echo "$OUT: $(ls -1 "$OUT"/${NAME}*.png | wc -l) files, $n distinct"
[ "$n" -lt 2 ] && echo "WARNING: camera did not move -- renders are identical" >&2
ls -1 "$OUT"/${NAME}*.png
