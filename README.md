# SurfacePattern

A Python plugin for Rhino 8 that draws curve patterns (perforation grids, halftone,
stamps) on surfaces and polysurfaces, with a live viewport preview driven from an
Eto panel. Built for product designers working on perforated panels and gradient
patterns.

## Features

- **Target picking** — surfaces, polysurfaces, and individual sub-faces (Ctrl+Shift pick)
- **Grid engine** — regular arrays of circles, slots, or hexagons
  - Grid types: square / staggered / triangular
  - Spacing means the perceived clear gap between shape edges (0 = shapes touching)
  - Position / size / rotation jitter with a fixed random seed
- **Halftone engine** — shape size modulated by distance to attractors (document points/curves)
  - Falloff profiles: linear / smooth / gaussian, with invert
  - Works instantly with zero attractors (falls back to each face's UV center)
  - Culling of shapes below a minimum size
- **Stamp engine** — user-registered closed planar curves as pattern units
  - Registration normalizes copies to unit bounds, so the size slider scales them
  - Array (grid lattice, cycle/random pick among multiple stamps), click-place with
    a mouse-following ghost preview, and freehand strokes pulled onto the face
  - Click-placed items are bulk-adjustable later via the Size/Rotation sliders
- **Placement modes**
  - `uv` — laid out in the face's UV space with first-derivative distortion compensation,
    so mm spacing holds on curved faces (default for a single face)
  - `world` — a lattice built on a common plane and projected onto the faces, giving
    seam-free patterns across polysurfaces (default for multiple faces)
- **Live preview** — DisplayConduit based, never writes to the document
  - While dragging: polyline approximations (draft, capped at 1500 shown);
    on release: NURBS curves pulled onto the surface (full)
- **Error UX** — full tracebacks go to `~/.surfacepattern/log.txt`; the panel shows
  a one-line red notice

## Requirements

- Rhino 8 (Windows) — embedded CPython 3 (ScriptEditor)
- numpy (accelerates halftone; a pure-Python fallback runs without it)

## Getting started

1. Open `SurfacePattern.rhproj` in the Rhino 8 ScriptEditor.
2. Run `commands/SurfacePattern_cmd.py` to open the panel.
3. In the panel:
   1. **Pick Targets** — select the target surfaces/polysurfaces
   2. Choose a mode — **Grid / Halftone / Stamp**
   3. **Layout** section — shape, gaps, grid type, jitter, and other lattice
      parameters shared by Grid and Halftone
   4. For Halftone, **Pick Attractors** (points/curves), then tune Radius and
      Size Min/Max
   5. Check the orange preview in the viewport → **Bake** (planned) to commit
      curves to the document

Development commands (visual checks only, not published):

| Command | Purpose |
| --- | --- |
| `SPDev_TestMapping_cmd` | Validates the mapping core — adds a perforation grid of circles sized to each face |
| `SPDev_TestConduit_cmd` | Validates the preview conduit — each run cycles off → draft → full |
| `SPDev_TestStamp_cmd` | Validates the stamp engine — registers star/circle test stamps, rejects a non-planar curve, previews a cycled array |

## Repository layout

```
SurfacePattern.rhproj            ScriptEditor project (libraries + commands + build settings)
commands/                        Rhino command entry points
  SurfacePattern_cmd.py          Opens the main panel
  SPDev_Test*.py                 Development validation commands
libraries/surfacepattern/        The plugin package
  core/
    session.py                   Session singleton (sticky), target/attractor picking, recompute orchestration
    mapping.py                   UV↔3D mapping, distortion compensation, world projection, curve placement/pullback (owns all surface evaluation)
    errors.py                    File logging + command-line notice
    bake.py                      Sole document writer (planned)
  engine/
    grid.py                      Regular lattice placement generation (parameter space only)
    halftone.py                  Attractor-distance size modulation
    shapes.py                    Unit shapes (NURBS + draft polyline)
    stamp.py                     Custom stamps: registration/normalization, array, click-place, freehand
  preview/
    conduit.py                   DisplayConduit two-tier rendering (draft/full)
  ui/
    panel.py                     Eto panel (modeless Form)
  io/
    presets.py                   Preset save/load (planned)
```

## Architecture principles (AGENTS.md summary)

- **Layering**: ui calls only the session; engines work purely in parameter space
  (no 3D curve creation, no document access); all surface evaluation
  (PointAt/FrameAt/PullToBrepFace, …) lives in `core/mapping.py`; preview never
  writes to the document; only `core/bake.py` may write to it
- **Normalized UV**: any UV crossing a module boundary is normalized to 0–1
- **Performance contract**: while dragging, draft only (no pullback); a full
  recompute runs once interaction ends (70 ms / 250 ms debounce)
- **Reload resilience**: session, conduit, and panel survive ScriptEditor module
  reloads via scriptcontext.sticky plus name-based type checks

## Status

| Milestone | State |
| --- | --- |
| Geometry core (picking, mapping, distortion compensation) | ✅ |
| Preview conduit (two-tier draft/full) | ✅ |
| Grid engine + panel wiring | ✅ |
| Eto panel (sliders, sections, debounce) | ✅ |
| Halftone engine | ✅ |
| Stamp engine (array / click-place / freehand) | ✅ |
| Preset save/load | ⏳ planned |
| Bake (commit to document) | ⏳ planned |
