#! python3
# Dev-only command: visual checks for UV<->3D mapping against test-geometry.3dm (not published).

import Rhino
import scriptcontext

from surfacepattern.core import mapping, session
from surfacepattern.engine import grid

TARGET_DIVISIONS = 15   # grid cells along the larger face dimension
HOLE_RATIO = 0.5        # hole diameter as a fraction of grid spacing (perf-panel look)


def spacing_for_face(record):
    """Derive a visual-test spacing from the face's physical size at its center."""
    scale = mapping.local_scale(record, 0.5, 0.5)
    if scale is None:
        return 0.0
    size_u, size_v = scale
    spacing = max(size_u, size_v) / TARGET_DIVISIONS
    if spacing <= 0.0:
        return 0.0
    return spacing


def main():
    """Pick targets and cover each face with a perf-panel style circle grid sized to the face."""
    current = session.get_session()
    if not session.pick_targets(current):
        Rhino.RhinoApp.WriteLine("SPDev_TestMapping: nothing selected.")
        return

    # Unit circle: diameter 1 at the origin; size = desired hole diameter.
    unit_circle = Rhino.Geometry.Circle(Rhino.Geometry.Plane.WorldXY, 0.5).ToNurbsCurve()

    undo_serial = scriptcontext.doc.BeginUndoRecord("SPDev TestMapping")
    added = 0
    fallbacks = 0
    spacing = spacing_for_face(current.targets[0])
    if spacing <= 0.0:
        Rhino.RhinoApp.WriteLine("SPDev_TestMapping: target has degenerate size.")
        scriptcontext.doc.EndUndoRecord(undo_serial)
        return
    hole_diameter = spacing * HOLE_RATIO
    original_params = dict(current.params)
    try:
        current.params.update({
            "pattern_mode": "grid",
            "placement_mode": "surface",
            "shape": "circle",
            "size": hole_diameter,
            "spacing_x": spacing - hole_diameter,
            "spacing_y": spacing - hole_diameter,
            "grid_type": "square",
            "jitter_position": 0.0,
            "jitter_size": 0.0,
            "jitter_rotation": 0.0,
            "seed": 0,
        })
        placements = grid.generate(current)
        for record, u, v, size, rotation in placements:
            curve, pulled = mapping.place_unit_curve(record, u, v, unit_circle, size, rotation)
            if curve is None:
                continue
            scriptcontext.doc.Objects.AddCurve(curve)
            added += 1
            if not pulled:
                fallbacks += 1
    finally:
        current.params = original_params
    scriptcontext.doc.EndUndoRecord(undo_serial)
    scriptcontext.doc.Views.Redraw()

    Rhino.RhinoApp.WriteLine(
        "SPDev_TestMapping: {} faces, {} projected-grid circles at spacing {:.1f}, "
        "{} pullback fallbacks".format(
            len(current.targets), added, spacing, fallbacks
        )
    )


main()
