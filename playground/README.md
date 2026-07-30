# playground

Scratch space for building new models. **Everything in here except this file is
gitignored** — put whatever you like in it and the repo stays clean.

That is the point: a model in progress drags along one-off diagnostic scripts,
half-finished variants, reference crops and multi-megabyte `.vox` output. None
of that belongs in version control, and having nowhere to put it is why it used
to end up committed at the repo root.

## Building something

Give each model its own subdirectory. From the repository root:

```
mkdir playground/lighthouse
$EDITOR playground/lighthouse/build.py
python3 playground/lighthouse/build.py
```

Read `../CLAUDE.md` first — it is the working procedure, and its "Verify before
reporting done" checklist is what catches the mistakes that a voxel count never
shows. `../examples/omri_cake.py` is the most complete worked example.

Two ways to reach `voxel.py`, which lives one level up from here:

- **A single script**, run directly. Add the repo root to the import path:
  ```python
  import os, sys
  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
      os.path.abspath(__file__)))))
  from voxel import Model, shapes
  ```
- **A package**, once it outgrows one file. Add an `__init__.py`, use relative
  imports between your own modules (`from . import palette`) and a bare
  `from voxel import Model`, then run it from the repository root as a module:
  ```
  python3 -m playground.lighthouse.build
  ```
  This is what `seattle_center/` does. It needs no `sys.path` fiddling at all,
  because running from the root already puts `voxel.py` on the path — and it
  keeps working if the directory later moves.

Don't hard-code the number of levels up to `voxel.py` outside of that first
snippet. `spaceship/shipkit.py` did, as a bare `".."`, and it broke the moment
the pack moved down one level into this directory; it now searches upward for
the directory containing `voxel.py`.

## What lives here now

| directory | what |
| --- | --- |
| `seattle_center/` | a 1024³ post-apocalyptic Seattle Center; multi-million voxels, a ~15 MB `.vox`, and its own `devscripts/`. Build with `python3 -m playground.seattle_center.build`. Watch memory — it is built to a ~7M voxel ceiling. |
| `spaceship/` | a modular-spaceship game asset pack: hulls, systems and enemies that snap together on sockets. `python3 playground/spaceship/build_all.py`. |
| `world_market/` | a 149×215×66 market street — eight regional stalls, shophouse facades, a fountain and a bakery, ~356k voxels in a single `Model`. Built by eight parallel authors against `spec.md`; `python3 -m playground.world_market.build`, then `python3 -m playground.world_market.check` for the 33-check suite. |

## Promoting to `examples/`

`examples/` is curated — it is what someone reads to learn the library, so the
bar is "worth reading", not "finished". To promote:

1. `git mv`-nothing: the source is untracked, so just `mv playground/thing
   examples/thing` and `git add` it.
2. Strip the scaffolding. The one-off diagnostic scripts stay behind or get
   deleted; a per-model checker that only ever applied to one asset is not
   library tooling. Only checks that exercise `voxel.py` itself belong in the
   top-level `devscripts/`.
3. Make it reproducible: seed every `random.Random(SEED)`, put the tunables in
   named constants at the top, and confirm a fresh run rewrites the same
   `.vox` byte for byte.
4. Commit the `.py` and the `.vox` together, so the file on disk matches the
   script that claims to build it.
