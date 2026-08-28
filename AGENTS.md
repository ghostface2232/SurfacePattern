# AGENTS.md — SurfacePattern

Context file for AI coding agents (Claude Code, Codex, and others) working on this repository.
Read this fully before writing or modifying any code.

## 1. What this project is

SurfacePattern is a Rhino 8 plugin that lets a designer draw curve patterns directly on surfaces and polysurfaces through a panel UI — no Grasshopper required.
The interaction model is Photoshop-filter-like: move a slider, see the pattern update on the 3D surface in the viewport immediately, then commit the result as baked curves or as an actual trim (perforation) of the surface.

Primary use cases: speaker-grille style perforation fields, gradient halftone patterns that attenuate with distance from attractor points/curves, regular hole arrays (circle / slot / hexagon), and custom curve stamps arrayed or hand-placed on doubly curved surfaces.

The end user is a product designer, not a developer. Visual quality of results and immediacy of feedback take priority over feature count.

## 2. Runtime and constraints

- Target: Rhino 8, Windows and Mac. The plugin is authored as a ScriptEditor project (.rhproj) and published to .rhp / .yak.
- Language: Python 3 (Rhino 8 embedded CPython). Every script starts with the `#! python3` directive.
- External PyPI packages are declared with a `# r: <package>` comment at the top of the script that needs them (e.g. `# r: numpy`). Use numpy only where vectorization gives a real win (bulk distance fields); everything else stays dependency-free.
- Geometry API: RhinoCommon via `import Rhino`. Document access via `scriptcontext.doc`. Do not use rhinoscriptsyntax in core/engine/preview modules; it is allowed only in throwaway dev commands.
- UI: Eto.Forms (Rhino 8 level). Parent all windows/panels to the Rhino document using Rhino.UI Eto extensions so they never fall behind the main window.
- Units: query the document unit system; all user-facing size/spacing parameters are in model units (mm assumed in docs and defaults).
- Editor-run vs published-plugin module resolution differs. Use package-path imports (`from surfacepattern.core import mapping`), never fragile relative imports. Dev-only commands are prefixed `SPDev_` and are excluded from publishing.

## 3. Repository layout

```
surfacepattern/
  AGENTS.md
  README.md
  SurfacePattern.rhproj          # ScriptEditor project (publish source)
  test-geometry.3dm              # canonical test file: plane, single-curved,
                                 # doubly-curved, trimmed srf, polysurface
  commands/
    SurfacePattern_cmd.py        # main command: opens the panel
    SPDev_TestMapping_cmd.py     # dev-only visual checks (not published)
    SPDev_TestConduit_cmd.py
  libraries/
    surfacepattern/
      core/
        session.py               # singleton state, recompute orchestration
        mapping.py               # UV<->3D, frames, surface metrics, distortion, pullback
        bake.py                  # bake curves, optional trim pipeline
      engine/
        grid.py                  # regular arrays (circle/slot/hex)
        halftone.py              # attractor-driven size/density falloff
        stamp.py                 # custom stamps: array, click-place, freehand
      preview/
        conduit.py               # DisplayConduit, draft/full rendering
      ui/
        panel.py                 # Eto panel, LabeledSlider, sections
      io/
        presets.py               # JSON preset save/load
```

## 4. Architecture and layering rules (strict)

```
 Eto panel (ui/)  -->  Session (core/session.py)  -->  Engines (engine/)
                                    |                        |
                                    |                (UV-space placements)
                                    v                        v
                            Mapping (core/mapping.py: UV->3D, pullback)
                                    |
                     +--------------+---------------+
                     v                              v
          Preview (preview/conduit.py)      Bake (core/bake.py)
          draws to pipeline only            writes to document only
```

- ui/ talks only to the session. It never imports engine, mapping, or bake directly.
- engine/ works purely in parameter space. An engine's `generate(session)` returns placements: tuples of (face_ref, u, v, size, rotation). Engines never create 3D curves and never touch the document.
- core/mapping.py owns every UV-to-world conversion, surface frame lookup, surface metric sampling, distortion compensation, and curve pullback. No other module calls Surface.PointAt / FrameAt / PullToBrepFace.
- preview/ never adds objects to the document. bake/ is the only module that writes document objects, and it wraps each bake in a single undo record.
- Session state lives in a singleton stored in `scriptcontext.sticky["surfacepattern_session"]` so it survives command re-entry. Access it only through `get_session()`.

## 5. Domain concepts an agent must know

- Placement modes. UV mode lays the pattern in a single face's normalized UV domain with lightweight derivative compensation — fast, but spacing can drift where the UV scale changes sharply. Uniform Surface mode uses area-weighted, Poisson-like minimum-distance sampling in 3D model space. Its topology adapts locally: points appear as the surface expands and disappear as it contracts, independent of U/V directions and continuously across selected faces. Default: UV for a single face, Uniform Surface for polysurfaces.
- Distortion compensation. Uniform UV spacing is non-uniform in 3D. Evaluate first derivatives (Surface.Evaluate) to get local du/dv arc-length scale and correct spacing and unit size so results look uniform in 3D. Re-evaluate per row on strongly curved faces.
- Trim awareness. Every generated UV point is validated with BrepFace.IsPointOnFace; points in trimmed-away regions are culled. Shapes whose UV bounds cross a seam or trim border are culled or inset (margin parameter) — pulled curves that straddle seams tear.
- Face orientation. Check BrepFace.OrientationIsReversed and flip frames so local Z always points outward.
- Unit shapes. Circle (radius size/2), stadium slot (length size, width ratio default 0.4), regular hexagon (circumscribed diameter size), or a user-registered planar closed curve normalized to unit bounds at the origin of the XY plane. Each unit shape must provide both a NURBS version and an 8-12 segment polyline approximation for draft preview.
- Attractors (halftone). Document points/curves referenced by object id. Re-resolve ids on every recompute (users move/delete them); silently drop dead ids. Min distance over all attractors feeds a falloff profile (linear / smoothstep / gaussian) with radius, invert flag, size min-max, and a cull threshold below which no shape is emitted. With zero attractors, fall back to per-face UV center so the mode shows results instantly.

## 6. Performance contract

Interactive feel is a hard requirement. The two-tier preview strategy is non-negotiable:

- Draft (while a slider is being dragged): polyline approximations only, no PullToBrepFace calls, hard cap on drawn shapes (default 1500, session parameter). Over the cap, subsample evenly and show "N of M shown" via Draw2dText.
- Full (on interaction end): real NURBS curves, pulled to faces.
- Slider events are debounced with an Eto UITimer (roughly 60-80 ms for draft; commit full recompute after roughly 250 ms of inactivity or on mouse-up / numeric entry).
- Never run pullback inside a draft recompute. Never redraw the document during drags; only `doc.Views.Redraw()` after updating the conduit cache.
- Trim (bake) is a heavy path: show StatusBar progress, support Esc cancel with full undo rollback, report failed curves by selecting them, and warn via dialog when hole count exceeds 300.
- If Python-side loops become the bottleneck despite caps, the sanctioned escape hatch is porting only conduit drawing / placement math to C# inside the same .rhproj — do not rewrite the project.

## 7. UI conventions

- Reference feel: Photoshop filter dialogs; restraint of Rhino / Alias / Plasticity panels. Dense but calm; no decorative chrome.
- Every numeric parameter is a LabeledSlider: label + slider + numeric field on one row, two-way synced, double-click restores default, out-of-range input clamps to nearest valid value with a brief visual notice.
- Panel order, top to bottom: target section (pick button, summary label, placement-mode dropdown) / pattern-mode segment (Grid, Halftone, Stamp) / mode parameter sections (collapsible) / preview toggle / fixed footer with preset dropdown + save and the Bake split-button (Curves only / Curves + Trim, with a keep-original-copy checkbox for trim, default on).
- Sliders never mutate the document. Only explicit button actions (Bake) write to it.
- Lock panel input during viewport picking operations (target pick, attractor pick, click-place, freehand draw).
- Wrap every Eto event handler with the shared try/except decorator that logs via Rhino.RhinoApp.WriteLine — Eto swallows handler exceptions silently.

## 8. Robustness rules

- Any document object the session references (targets, attractors, stamps) may be deleted or transformed at any time. Re-resolve by id on every recompute; prune dead references and refresh panel labels; empty target list clears the preview and shows guidance text.
- Subscribe to document close/new events to tear down the session and disable the conduit.
- Conduit must override CalculateBoundingBox (union of all preview curves) or geometry gets clipped; participate in Zoom Extents via the corresponding override.
- Pullback failures: fall back to the frame-planar curve, flag it, and surface the count to the user; for trim, retry pullback at half document tolerance before giving up on a curve.
- Baked curves carry their generating parameters as a JSON UserString for traceability, and land on per-operation sublayers under a SurfacePattern parent layer.

## 9. Code style

- snake_case for functions/variables, PascalCase for classes, one-line docstring on every public function.
- Normalize all surface domains to 0-1 before use; never pass raw Interval values across module boundaries.
- Random features (jitter, stamp selection) always consume an explicit seed from session parameters so results are reproducible.
- No test runner in this repo (code only runs inside Rhino). Verification is done through SPDev_ commands against test-geometry.3dm; when you add a nontrivial core/engine capability, add or extend an SPDev_ command that makes it visually checkable.
- Keep diffs minimal and layered: a UI change must not silently edit engine code, and vice versa.

## 10. Definition of done for any change

1. Runs from ScriptEditor against test-geometry.3dm without errors on all five geometry cases (plane, single-curved, doubly-curved, trimmed, polysurface).
2. Draft preview stays visually immediate (target under ~100 ms perceived) at default caps.
3. No document mutation outside bake.py; bake undoable in a single step.
4. Panel labels/state stay truthful after target or attractor deletion.
5. Publishing to .rhp still succeeds (check on milestone changes: imports must not rely on editor-only paths).
