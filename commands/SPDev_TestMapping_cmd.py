#! python3
# Dev-only command: visual checks for UV<->3D mapping against test-geometry.3dm (not published).

import Rhino
import scriptcontext

from surfacepattern.core import mapping, session

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


def equal_surface_grid(record, spacing):
    """Arc-length-spaced normalized UV points for visual Uniform Surface checks."""
    points = []
    rows = mapping.equal_arc_parameters(record, "v", 0.5, spacing, max_count=1000)
    for v in rows:
        for u in mapping.equal_arc_parameters(record, "u", v, spacing, max_count=1000):
            if mapping.is_point_on_face(record, u, v):
                points.append((u, v))
    return points


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
    for record in current.targets:
        spacing = spacing_for_face(record)
        if spacing <= 0.0:
            Rhino.RhinoApp.WriteLine(
                "SPDev_TestMapping: face {} has degenerate size, skipped.".format(record.face_index)
            )
            continue
        hole_diameter = spacing * HOLE_RATIO
        points = equal_surface_grid(record, spacing)
        for u, v in points:
            curve, pulled = mapping.place_unit_curve(record, u, v, unit_circle, hole_diameter, 0.0)
            if curve is None:
                continue
            scriptcontext.doc.Objects.AddCurve(curve)
            added += 1
            if not pulled:
                fallbacks += 1
        Rhino.RhinoApp.WriteLine(
            "SPDev_TestMapping: face {}: {} Uniform Surface points, "
            "spacing {:.1f}, hole d={:.1f}".format(
                record.face_index, len(points), spacing, hole_diameter
            )
        )
    scriptcontext.doc.EndUndoRecord(undo_serial)
    scriptcontext.doc.Views.Redraw()

    Rhino.RhinoApp.WriteLine(
        "SPDev_TestMapping: {} faces, {} circles added, {} pullback fallbacks, "
        "suggested mode: {}".format(
            len(current.targets), added, fallbacks, current.params.get("placement_mode")
        )
    )


main()
