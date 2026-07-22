#! python3
# Dev-only command: visual checks for UV<->3D mapping against test-geometry.3dm (not published).

import Rhino
import scriptcontext

from surfacepattern.core import mapping, session


def main():
    """Pick targets, lay a 20x20 UV grid per face, place r=2mm circles, add them to the doc."""
    current = session.get_session()
    if not session.pick_targets(current):
        Rhino.RhinoApp.WriteLine("SPDev_TestMapping: nothing selected.")
        return

    # Unit circle: diameter 1 at the origin; size=4.0 yields radius 2 (AGENTS: circle radius = size/2).
    unit_circle = Rhino.Geometry.Circle(Rhino.Geometry.Plane.WorldXY, 0.5).ToNurbsCurve()

    undo_serial = scriptcontext.doc.BeginUndoRecord("SPDev TestMapping")
    added = 0
    fallbacks = 0
    for record in current.targets:
        for u, v in mapping.uv_grid(record, 20, 20):
            curve, pulled = mapping.place_unit_curve(record, u, v, unit_circle, 4.0, 0.0)
            if curve is None:
                continue
            scriptcontext.doc.Objects.AddCurve(curve)
            added += 1
            if not pulled:
                fallbacks += 1
    scriptcontext.doc.EndUndoRecord(undo_serial)
    scriptcontext.doc.Views.Redraw()

    Rhino.RhinoApp.WriteLine(
        "SPDev_TestMapping: {} faces, {} circles added, {} pullback fallbacks, "
        "suggested mode: {}".format(
            len(current.targets), added, fallbacks, current.params.get("placement_mode")
        )
    )


main()
